from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fsspec.implementations.memory import MemoryFileSystem

from metrictrace.config import load_settings
from metrictrace.ingestion.olist_source import DAILY_TABLES
from metrictrace.ingestion.runner import Ingestion, IngestionError
from metrictrace.lake import LakeClient
from tests.conftest import apply, fixture_settings_env

DAY = date(2018, 3, 1)


@pytest.fixture
def ingestion(env: pytest.MonkeyPatch) -> Ingestion:
    apply(env, fixture_settings_env())
    settings = load_settings(env_file=None)
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    fs.mkdir(settings.lake_bucket)
    return Ingestion(
        settings=settings,
        lake=LakeClient(settings, fs=fs),
        run_id="test-run",
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_all_sources_for_one_day(ingestion: Ingestion) -> None:
    results = ingestion.run([DAY], ["olist", "fx", "holidays"])
    sources = [r.source for r in results]
    assert sources[:2] == ["olist/product_category_name_translation", "olist/geolocation"]
    assert "holidays" in sources
    assert sources.count("fx") == 1
    assert {f"olist/{t}" for t in DAILY_TABLES} <= set(sources)
    assert all(r.status in {"ok", "empty"} for r in results if r.source != "olist/geolocation")
    orders = next(r for r in results if r.source == "olist/orders")
    assert orders.row_count > 0


def test_reference_tables_load_once_unless_forced(ingestion: Ingestion) -> None:
    ingestion.run([DAY], ["olist"])
    again = ingestion.run([DAY], ["olist"])
    assert not any("geolocation" in r.source for r in again)
    forced = ingestion.run([DAY], ["olist"], force_reference=True)
    assert any("geolocation" in r.source for r in forced)


def test_rerun_gives_identical_checksums(ingestion: Ingestion) -> None:
    first = {r.source: r.checksum for r in ingestion.run([DAY], ["olist", "fx"])}
    second = {r.source: r.checksum for r in ingestion.run([DAY], ["olist", "fx"])}
    assert {k: v for k, v in first.items() if k in second} == second


def test_days_outside_window_are_refused(ingestion: Ingestion) -> None:
    with pytest.raises(IngestionError, match="outside the replay window"):
        ingestion.run([date(2017, 1, 1)], ["olist"])


def test_unknown_source_is_refused(ingestion: Ingestion) -> None:
    with pytest.raises(IngestionError, match="unknown sources"):
        ingestion.run([DAY], ["weather"])
