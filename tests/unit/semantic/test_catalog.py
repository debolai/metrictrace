from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from metrictrace.semantic.catalog import CatalogError, catalog_from_meta
from tests.conftest import FIXTURES

ROOT = FIXTURES.parents[1]
META: dict[str, Any] = json.loads((FIXTURES / "cube" / "meta.json").read_text())
TERMS = ROOT / "cube" / "ambiguous_terms.yaml"

SPEC_METRICS = {
    "gmv", "revenue_incl_freight", "payments_collected", "orders", "aov", "cancellation_rate",
    "on_time_delivery_rate", "avg_delivery_days", "avg_review_score", "negative_review_rate",
    "active_sellers", "returning_customer_rate",
}  # fmt: skip


def meta_copy() -> dict[str, Any]:
    return copy.deepcopy(META)


def measure(meta: dict[str, Any], member: str) -> dict[str, Any]:
    view, _ = member.split(".")
    cube = next(c for c in meta["cubes"] if c["name"] == view)
    return next(m for m in cube["measures"] if m["name"] == member)


def test_recorded_meta_builds_the_full_catalog() -> None:
    catalog = catalog_from_meta(META, TERMS)
    assert set(catalog.metrics) >= SPEC_METRICS
    assert {"gmv_usd", "revenue_incl_freight_usd"} <= set(catalog.metrics)
    gmv = catalog.get("gmv")
    assert (gmv.view, gmv.unit, gmv.grain, gmv.metric_type) == ("sales", "BRL", "item", "sum")
    assert "category" in gmv.decomposable_by
    aov = catalog.get("aov")
    assert (aov.numerator, aov.denominator) == ("gmv", "non_cancelled_orders")
    assert aov.member() == "orders.aov"
    assert aov.title == "Average order value"


def test_only_views_are_public() -> None:
    assert {c["type"] for c in META["cubes"]} == {"view"}


def test_maturity_windows_follow_the_spec() -> None:
    catalog = catalog_from_meta(META)
    assert catalog.get("cancellation_rate").maturity_days == 30
    assert catalog.get("on_time_delivery_rate").maturity_days == 60
    assert catalog.get("avg_review_score").maturity_days == 45
    assert catalog.get("gmv").maturity_days == 0


@pytest.mark.parametrize(
    "field", ["unit", "synonyms", "higherIsBetter", "owner", "grain", "maturityDays",
              "decomposableBy", "metricType"],
)  # fmt: skip
def test_missing_governance_field_fails(field: str) -> None:
    meta = meta_copy()
    del measure(meta, "sales.gmv")["meta"][field]
    with pytest.raises(CatalogError, match=r"sales\.gmv"):
        catalog_from_meta(meta)


def test_missing_description_fails() -> None:
    meta = meta_copy()
    measure(meta, "sales.gmv")["description"] = ""
    with pytest.raises(CatalogError, match="missing description"):
        catalog_from_meta(meta)


def test_decomposition_dimension_must_exist_in_view() -> None:
    meta = meta_copy()
    measure(meta, "orders.orders")["meta"]["decomposableBy"].append("category")
    with pytest.raises(CatalogError, match="decomposable_by not in view"):
        catalog_from_meta(meta)


def test_ratio_needs_components_present_in_view() -> None:
    meta = meta_copy()
    measure(meta, "orders.aov")["meta"]["denominator"] = "nonexistent"
    with pytest.raises(CatalogError, match="component nonexistent"):
        catalog_from_meta(meta)
    meta = meta_copy()
    del measure(meta, "orders.aov")["meta"]["numerator"]
    with pytest.raises(CatalogError, match="numerator and denominator"):
        catalog_from_meta(meta)


def test_public_cube_is_rejected() -> None:
    meta = meta_copy()
    meta["cubes"][0]["type"] = "cube"
    with pytest.raises(CatalogError, match="only views may be public"):
        catalog_from_meta(meta)


def test_resolve_names_synonyms_and_ambiguous_terms() -> None:
    catalog = catalog_from_meta(META, TERMS)
    assert catalog.resolve("GMV") == ["gmv"]
    assert catalog.resolve("basket size") == ["aov"]
    assert catalog.resolve("revenue") == ["gmv", "revenue_incl_freight", "payments_collected"]
    assert catalog.ambiguous_terms["revenue"].default == "gmv"
    assert catalog.resolve("weather") == []


def test_ambiguous_term_with_unknown_metric_fails(tmp_path: Path) -> None:
    terms = tmp_path / "terms.yaml"
    terms.write_text("terms:\n  profit:\n    candidates: [margin]\n    default: margin\n")
    with pytest.raises(CatalogError, match="profit"):
        catalog_from_meta(META, terms)


def test_unknown_metric_lookup_fails() -> None:
    with pytest.raises(CatalogError, match="unknown metric"):
        catalog_from_meta(META).get("profit")
