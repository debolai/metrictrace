"""Phase 2 gate: ingestion of the fixture into the real object store (`make up`)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from metrictrace.config import Settings, load_settings
from metrictrace.ingestion.landing import DropResult
from metrictrace.ingestion.olist_source import DAILY_TABLES
from metrictrace.ingestion.runner import Ingestion
from metrictrace.lake import LakeClient, build_filesystem, connect
from tests.conftest import FIXTURES, fixture_settings_env

pytestmark = pytest.mark.integration

START = date(2018, 2, 1)
DAYS = [START + timedelta(days=offset) for offset in range(31)]


@pytest.fixture(scope="module")
def settings() -> Iterator[Settings]:
    """Fixture settings pointed at a throwaway bucket in the running object store."""
    base = load_settings()  # endpoint and credentials from .env / CI
    overrides = {
        k: v
        for k, v in fixture_settings_env().items()
        if k not in {"LAKE_ENDPOINT_URL", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY", "AWS_REGION"}
    }
    overrides["LAKE_BUCKET"] = f"metrictrace-it-{uuid.uuid4().hex[:12]}"
    with pytest.MonkeyPatch.context() as patch:
        for name, value in overrides.items():
            patch.setenv(name, value)
        settings = load_settings()
    assert settings.lake_endpoint_url == base.lake_endpoint_url
    fs = build_filesystem(settings)
    fs.mkdir(settings.lake_bucket)
    yield settings
    fs.rm(settings.lake_bucket, recursive=True)


def run(
    settings: Settings, days: list[date], sources: list[str], source_path: Path | None = None
) -> list[DropResult]:
    if source_path is not None:
        settings = settings.model_copy(update={"olist_source_path": source_path})
    ingestion = Ingestion(
        settings=settings,
        lake=LakeClient(settings),
        run_id=f"it-{uuid.uuid4().hex[:6]}",
        ingested_at=datetime.now(UTC),
    )
    return ingestion.run(days, sources)


@pytest.fixture(scope="module")
def backfill(settings: Settings) -> list[DropResult]:
    return run(settings, DAYS, ["olist", "fx", "holidays"])


def current_orders(settings: Settings) -> list[tuple[str, str]]:
    con = connect(settings)
    try:
        return con.sql(
            f"""
            select order_id, order_status
            from read_parquet('s3://{settings.lake_bucket}/raw/olist/orders/*/*.parquet',
                              hive_partitioning = true)
            qualify row_number() over (partition by order_id order by drop_date desc) = 1
            order by order_id
            """
        ).fetchall()
    finally:
        con.close()


def test_backfill_writes_a_meta_record_per_daily_table_per_day(
    settings: Settings, backfill: list[DropResult]
) -> None:
    lake = LakeClient(settings)
    for table in DAILY_TABLES:
        assert len(lake.list(f"_meta/ingestion/olist/{table}")) == 31, table
    assert len(lake.list("_meta/ingestion/fx")) == 31


def test_partitions_exist_exactly_for_days_with_rows(
    settings: Settings, backfill: list[DropResult]
) -> None:
    lake = LakeClient(settings)
    for table in DAILY_TABLES:
        with_rows = {
            r.partition for r in backfill if r.source == f"olist/{table}" and r.row_count > 0
        }
        landed = {key.split("/")[3] for key in lake.list(f"raw/olist/{table}")}
        assert landed == with_rows, table
    assert lake.list("raw/olist/geolocation")
    assert lake.list("raw/holidays/year=2018")


def test_rerunning_an_old_date_keeps_checksum_and_current_state(
    settings: Settings, backfill: list[DropResult]
) -> None:
    before_state = current_orders(settings)
    before = {r.source: r.checksum for r in backfill if r.partition == "drop_date=2018-02-01"}
    rerun = run(settings, [START], ["olist", "fx"])
    after = {r.source: r.checksum for r in rerun if r.partition == "drop_date=2018-02-01"}
    assert after == before
    assert current_orders(settings) == before_state
    statuses = {status for _, status in before_state}
    assert "delivered" in statuses  # later versions exist and still win over the re-run


def test_corrupted_drop_is_quarantined_and_recorded(
    settings: Settings, backfill: list[DropResult], tmp_path: Path
) -> None:
    source = tmp_path / "olist"
    shutil.copytree(FIXTURES / "olist", source)
    items = source / "olist_order_items_dataset.csv"
    lines = items.read_text().splitlines()
    # Corrupt every price: the day's drop must be rejected, not landed.
    header = lines[0].split(",")
    price = header.index("price")
    corrupted = [lines[0]] + [
        ",".join(v if i != price else "not-a-number" for i, v in enumerate(line.split(",")))
        for line in lines[1:]
    ]
    items.write_text("\n".join(corrupted) + "\n")

    day = date(2018, 2, 5)
    results = run(settings, [day], ["olist"], source_path=source)
    items_result = next(r for r in results if r.source == "olist/order_items")
    assert items_result.status == "failed"
    lake = LakeClient(settings)
    meta = lake.read_json("_meta/ingestion/olist/order_items/drop_date=2018-02-05.json")
    assert meta["status"] == "failed"
    assert any("price" in reason for reason in meta["reasons"])
    assert lake.list("quarantine/olist/order_items/drop_date=2018-02-05")
    assert lake.list("raw/olist/order_items/drop_date=2018-02-05") == []

    # Re-ingesting the clean source restores the day.
    restored = run(settings, [day], ["olist"])
    assert next(r for r in restored if r.source == "olist/order_items").status == "ok"
    assert lake.list("quarantine/olist/order_items/drop_date=2018-02-05") == []
