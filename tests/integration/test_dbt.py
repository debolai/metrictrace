"""Phase 3 gate: the fixture replayed into a fresh bucket, transformed with dbt."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pandas as pd
import pytest

from metrictrace import transform
from metrictrace.config import Settings, load_settings
from metrictrace.ingestion.runner import Ingestion
from metrictrace.lake import LakeClient, build_filesystem, connect
from tests.conftest import FIXTURES, fixture_settings_env

pytestmark = pytest.mark.integration

MARTS = [
    "dim_customers",
    "dim_date",
    "dim_products",
    "dim_sellers",
    "fct_order_items",
    "fct_orders",
    "fct_payments",
    "fct_reviews",
    "mart_data_health_daily",
    "mart_dbt_run",
]


@pytest.fixture(scope="module")
def built() -> Iterator[tuple[Settings, transform.TransformResult]]:
    load_settings()  # endpoint and credentials from .env / CI
    overrides = {
        k: v
        for k, v in fixture_settings_env().items()
        if k not in {"LAKE_ENDPOINT_URL", "RUSTFS_ACCESS_KEY", "RUSTFS_SECRET_KEY", "AWS_REGION"}
    }
    overrides["LAKE_BUCKET"] = f"metrictrace-dbt-{uuid.uuid4().hex[:12]}"
    with pytest.MonkeyPatch.context() as patch:
        for name, value in overrides.items():
            patch.setenv(name, value)
        settings = load_settings()
    fs = build_filesystem(settings)
    fs.mkdir(settings.lake_bucket)
    lake = LakeClient(settings)
    Ingestion(settings, lake, "it-ingest", datetime.now(UTC)).run(
        [day.date() for day in pd.date_range(settings.sim_start_date, settings.sim_end_date)],
        ["olist", "fx", "holidays"],
    )
    result = transform.transform(settings, lake, "it-transform")
    yield settings, result
    fs.rm(settings.lake_bucket, recursive=True)


def test_build_succeeds(built: tuple[Settings, transform.TransformResult]) -> None:
    _, result = built
    assert result.success
    assert result.freshness_success


def test_marts_land_one_folder_per_model(built: tuple[Settings, transform.TransformResult]) -> None:
    settings, _ = built
    keys = LakeClient(settings).list("marts")
    assert keys == [f"marts/{m}/{m}.parquet" for m in MARTS]


def test_artifacts_are_published(built: tuple[Settings, transform.TransformResult]) -> None:
    settings, result = built
    lake = LakeClient(settings)
    for name in ("manifest.json", "run_results.json", "sources.json"):
        assert f"_meta/dbt/run_id=it-transform/{name}" in result.published
    latest = lake.read_json("_meta/dbt/latest.json")
    assert latest["run_id"] == "it-transform"
    assert latest["success"] is True
    results = json.loads(lake.read_bytes("_meta/dbt/run_id=it-transform/run_results.json"))
    statuses = {r["status"] for r in results["results"]}
    assert "error" not in statuses
    assert "fail" not in statuses


def test_mart_totals_match_the_oracle(built: tuple[Settings, transform.TransformResult]) -> None:
    """Early end-to-end parity on base totals; full metric parity runs through Cube (Phase 4)."""
    settings, _ = built
    golden = json.loads((FIXTURES / "golden.json").read_text())
    expected = golden["as_of"][settings.sim_end_date.isoformat()]["overall"]
    con = connect(settings)
    try:
        row = con.sql(
            f"""
            select
                count(*),
                sum(items_value) filter (where not is_cancelled),
                sum(items_value_usd) filter (where not is_cancelled),
                sum(payment_value) filter (where not is_cancelled),
                avg(case when is_cancelled then 1.0 else 0.0 end)
            from read_parquet('s3://{settings.lake_bucket}/marts/fct_orders/*.parquet')
            """
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    orders, gmv, gmv_usd, payments, cancelled = row
    assert orders == expected["orders"]
    assert gmv == pytest.approx(expected["gmv"], rel=1e-9)
    assert gmv_usd == pytest.approx(expected["gmv_usd"], rel=1e-9)
    assert payments == pytest.approx(expected["payments_collected"], rel=1e-9)
    assert cancelled == pytest.approx(expected["cancellation_rate"], rel=1e-9)
