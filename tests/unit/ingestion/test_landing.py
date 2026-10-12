from __future__ import annotations

import io
from datetime import UTC, datetime

import pandas as pd
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from metrictrace.config import load_settings
from metrictrace.ingestion.landing import PART_FILE, land_drop, record_hashes, target_for
from metrictrace.lake import LakeClient
from tests.conftest import VALID_LOCAL, apply
from tests.unit.ingestion.test_contracts import many, orders
from tests.unit.ingestion.test_lifecycle import order

FIRST = datetime(2026, 1, 1, tzinfo=UTC)
SECOND = datetime(2026, 1, 2, tzinfo=UTC)
TARGET = target_for("olist/orders", "orders", "drop_date=2018-03-01", "olist_orders_dataset.csv")


@pytest.fixture
def lake(env: pytest.MonkeyPatch) -> LakeClient:
    apply(env, VALID_LOCAL)
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    fs.mkdir("metrictrace-lake")
    return LakeClient(load_settings(env_file=None), fs=fs)


def read_parquet(lake: LakeClient, key: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(lake.read_bytes(key)))


def test_layout_follows_spec() -> None:
    assert TARGET.raw_prefix == "raw/olist/orders/drop_date=2018-03-01"
    assert TARGET.quarantine_prefix == "quarantine/olist/orders/drop_date=2018-03-01"
    assert TARGET.meta_key == "_meta/ingestion/olist/orders/drop_date=2018-03-01.json"
    reference = target_for("olist/geolocation", "geolocation", "", "geo.csv")
    assert reference.raw_prefix == "raw/olist/geolocation"
    assert reference.meta_key == "_meta/ingestion/olist/geolocation/reference.json"


def test_valid_drop_lands_with_lineage(lake: LakeClient) -> None:
    result = land_drop(lake, TARGET, orders(*many(3)), FIRST, "run-1")
    assert result.status == "ok"
    landed = read_parquet(lake, f"{TARGET.raw_prefix}/{PART_FILE}")
    assert len(landed) == 3
    assert {"_ingested_at", "_source_file", "_record_hash"} <= set(landed.columns)
    assert set(landed["_source_file"]) == {"olist_orders_dataset.csv"}
    assert "drop_date" not in landed.columns  # it lives in the partition path
    meta = lake.read_json(TARGET.meta_key)
    assert meta["row_count"] == 3
    assert meta["checksum"] == result.checksum


def test_rerun_is_idempotent_and_checksum_ignores_ingested_at(lake: LakeClient) -> None:
    first = land_drop(lake, TARGET, orders(*many(3)), FIRST, "run-1")
    second = land_drop(lake, TARGET, orders(*many(3)), SECOND, "run-2")
    assert first.checksum == second.checksum
    assert lake.list(TARGET.raw_prefix) == [f"{TARGET.raw_prefix}/{PART_FILE}"]
    assert lake.read_json(TARGET.meta_key)["run_id"] == "run-2"


def test_changed_content_changes_checksum(lake: LakeClient) -> None:
    first = land_drop(lake, TARGET, orders(*many(3)), FIRST, "run-1")
    changed = land_drop(lake, TARGET, orders(*many(2)), FIRST, "run-1")
    assert first.checksum != changed.checksum


def test_quarantined_rows_land_beside_with_reason(lake: LakeClient) -> None:
    rows = [*many(30), order("bad", status="lost")]
    result = land_drop(lake, TARGET, orders(*rows), FIRST, "run-1")
    assert (result.status, result.row_count, result.quarantine_count) == ("ok", 30, 1)
    quarantined = read_parquet(lake, f"{TARGET.quarantine_prefix}/{PART_FILE}")
    assert list(quarantined["order_id"]) == ["bad"]
    assert quarantined.columns[-1] == "_quarantine_reason"


def test_rejected_drop_lands_nothing_in_raw(lake: LakeClient) -> None:
    land_drop(lake, TARGET, orders(*many(3)), FIRST, "run-1")
    rows = [*many(3), order("bad", status="lost")]
    result = land_drop(lake, TARGET, orders(*rows), SECOND, "run-2")
    assert result.status == "failed"
    assert lake.list(TARGET.raw_prefix) == []
    assert lake.read_json(TARGET.meta_key)["status"] == "failed"


def test_empty_drop_records_meta_only(lake: LakeClient) -> None:
    result = land_drop(lake, TARGET, orders(), FIRST, "run-1")
    assert result.status == "empty"
    assert lake.list(TARGET.raw_prefix) == []
    assert lake.exists(TARGET.meta_key)


def test_record_hash_depends_on_values_only() -> None:
    frame = orders(*many(2))
    assert record_hashes(frame).equals(record_hashes(frame.copy()))
    assert record_hashes(frame).iloc[0] != record_hashes(frame).iloc[1]
