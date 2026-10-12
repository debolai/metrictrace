"""Phase 4 gate: Cube over the fixture lake equals the independent oracle.

The lake is built in two stages, as of the oracle's mid-window date and then as of the end
of the replay. Matching both proves late-arriving data is handled, and that Cube's refresh
key picks up each new dbt build. Run with `make test-semantic`, which points Cube at the
fixture bucket first.
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from metrictrace import transform
from metrictrace.config import Settings, load_settings
from metrictrace.ingestion.runner import Ingestion
from metrictrace.lake import LakeClient, connect
from metrictrace.semantic.catalog import Catalog, MetricDefinition, catalog_from_meta
from metrictrace.semantic.cube_client import CubeClient, CubeQuery, TimeDimension
from tests.conftest import FIXTURE_MANIFEST, FIXTURES

pytestmark = pytest.mark.integration

GOLDEN: dict[str, Any] = json.loads((FIXTURES / "golden.json").read_text())["as_of"]
AS_OF = [date.fromisoformat(d) for d in FIXTURE_MANIFEST["as_of_dates"]]
REL = 1e-9
# Cube re-checks a SQL refresh key at most every 10 seconds on DuckDB (2 minutes on
# Athena), so answers converge to a new build within that interval, not instantly.
REFRESH_DEADLINE_SECONDS = 60


def days(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.date_range(start, end)]


def scalar(value: Any) -> float | None:
    return None if value is None else float(value)


def close(actual: float | None, expected: float | None) -> bool:
    if expected is None or actual is None:
        return actual is expected
    return math.isclose(actual, expected, rel_tol=REL, abs_tol=1e-12)


def measure_metrics(client: CubeClient, catalog: Catalog) -> dict[str, Any]:
    """Every catalog metric, overall and by purchase month, as Cube answers now."""
    overall: dict[str, float | None] = {}
    by_month: dict[str, dict[str, float | None]] = {}
    views: dict[str, list[MetricDefinition]] = {}
    for metric in catalog.metrics.values():
        views.setdefault(metric.view, []).append(metric)
    for view, metrics in views.items():
        members = [m.member() for m in metrics]
        total = client.load(CubeQuery(measures=members)).rows[0]
        for m in metrics:
            overall[m.name] = scalar(total[m.member()])
        monthly = client.load(
            CubeQuery(
                measures=members,
                time_dimensions=[
                    TimeDimension(dimension=f"{view}.purchase_date", granularity="month")
                ],
            )
        ).rows
        for row in monthly:
            month = str(row[f"{view}.purchase_date.month"])[:7]
            for m in metrics:
                by_month.setdefault(month, {})[m.name] = scalar(row[m.member()])
    return {"overall": overall, "by_month": by_month}


def wait_for_refresh(client: CubeClient, settings: Settings) -> float:
    """Poll Cube until it serves the order count now in the lake; returns seconds waited."""
    con = connect(settings)
    try:
        row = con.sql(
            f"select count(*) from read_parquet('s3://{settings.lake_bucket}/marts/fct_orders/*.parquet')"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    started = time.monotonic()
    while True:
        served = client.load(CubeQuery(measures=["orders.orders"])).rows[0]["orders.orders"]
        if int(served) == row[0]:
            return time.monotonic() - started
        if time.monotonic() - started > REFRESH_DEADLINE_SECONDS:
            pytest.fail(f"Cube still serves {served} orders; the lake has {row[0]}")
        time.sleep(1)


@pytest.fixture(scope="module")
def settings() -> Settings:
    settings = load_settings()
    assert settings.lake_bucket == "metrictrace-fixture", "run via `make test-semantic`"
    return settings


@pytest.fixture(scope="module")
def client(settings: Settings) -> CubeClient:
    return CubeClient(settings)


@pytest.fixture(scope="module")
def staged(settings: Settings, client: CubeClient) -> dict[str, dict[str, Any]]:
    lake = LakeClient(settings)
    lake.ensure_bucket(create=True)
    for prefix in ("raw", "quarantine", "_meta", "marts"):
        lake.delete(prefix)
    results: dict[str, dict[str, Any]] = {}
    start = settings.sim_start_date
    for as_of in AS_OF:
        Ingestion(
            settings, lake, f"semantic-{as_of}", pd.Timestamp.now(tz="UTC").to_pydatetime()
        ).run(days(start, as_of), ["olist", "fx", "holidays"])
        built = transform.transform(settings, lake, f"semantic-{as_of}")
        assert built.success, f"dbt build failed as of {as_of}"
        wait_for_refresh(client, settings)
        catalog = catalog_from_meta(client.meta(), settings.ambiguous_terms_path)
        results[as_of.isoformat()] = measure_metrics(client, catalog)
        start = as_of + timedelta(days=1)
    return results


@pytest.fixture(scope="module")
def catalog(settings: Settings, client: CubeClient) -> Catalog:
    return catalog_from_meta(client.meta(), settings.ambiguous_terms_path)


@pytest.mark.parametrize("as_of", [d.isoformat() for d in AS_OF])
def test_every_metric_matches_the_oracle(staged: dict[str, Any], as_of: str) -> None:
    actual, expected = staged[as_of]["overall"], GOLDEN[as_of]["overall"]
    mismatches = {m: (actual.get(m), v) for m, v in expected.items() if not close(actual.get(m), v)}
    assert not mismatches, f"as of {as_of}: metric -> (cube, oracle): {mismatches}"


@pytest.mark.parametrize("as_of", [d.isoformat() for d in AS_OF])
def test_monthly_values_match_the_oracle(staged: dict[str, Any], as_of: str) -> None:
    actual, expected = staged[as_of]["by_month"], GOLDEN[as_of]["by_month"]
    assert sorted(actual) == sorted(expected)
    mismatches = {
        (month, m): (actual[month].get(m), v)
        for month, values in expected.items()
        for m, v in values.items()
        if not close(actual[month].get(m), v)
    }
    assert not mismatches, f"as of {as_of}: (month, metric) -> (cube, oracle): {mismatches}"


def test_new_builds_change_cube_answers(staged: dict[str, Any]) -> None:
    """The refresh key follows dbt: after the second build Cube serves the new data."""
    mid, end = (staged[d.isoformat()]["overall"] for d in AS_OF)
    assert mid["orders"] != end["orders"]


def test_catalog_metadata_is_complete(catalog: Catalog) -> None:
    spec = {
        "gmv", "revenue_incl_freight", "payments_collected", "orders", "aov", "cancellation_rate",
        "on_time_delivery_rate", "avg_delivery_days", "avg_review_score", "negative_review_rate",
        "active_sellers", "returning_customer_rate",
    }  # fmt: skip
    assert spec <= set(catalog.metrics)
    assert catalog.ambiguous_terms["revenue"].default == "gmv"


def test_decomposition_is_additive(
    staged: dict[str, Any], client: CubeClient, catalog: Catalog
) -> None:
    """Spec rule: segments of every decomposable metric add up exactly to the total.

    Ratios and averages are checked through their numerator and denominator, which is what
    shift-share decomposes.
    """
    failures = []
    for metric in catalog.metrics.values():
        parts = [metric.numerator, metric.denominator] if metric.numerator else [metric.name]
        members = [metric.member(p) for p in parts]
        total = client.load(CubeQuery(measures=members)).rows[0]
        for dimension in metric.decomposable_by:
            rows = client.load(
                CubeQuery(measures=members, dimensions=[metric.member(dimension)])
            ).rows
            for member in members:
                summed = sum(float(r[member] or 0) for r in rows)
                if not math.isclose(summed, float(total[member]), rel_tol=REL):
                    failures.append((metric.name, dimension, member, summed, total[member]))
    assert not failures, failures


def test_generated_sql_is_available_for_audit(client: CubeClient, settings: Settings) -> None:
    sql = client.sql(CubeQuery(measures=["sales.gmv"], dimensions=["sales.category"]))
    assert "metrictrace-fixture/marts/fct_order_items" in sql


def test_recorded_meta_matches_the_live_model(client: CubeClient, settings: Settings) -> None:
    """Unit tests use data/fixtures/cube/meta.json; it must not drift from the live model."""
    recorded = json.loads((FIXTURES / "cube" / "meta.json").read_text())
    live = catalog_from_meta(client.meta(), settings.ambiguous_terms_path)
    assert catalog_from_meta(recorded, settings.ambiguous_terms_path) == live, (
        "re-record with: make record-cube-meta"
    )
