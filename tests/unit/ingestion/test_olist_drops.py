from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from metrictrace.ingestion.olist_drops import ReplayPlan, dedupe_geolocation
from tests.unit.ingestion.test_lifecycle import COLUMNS, order


def tables() -> dict[str, pd.DataFrame]:
    orders = pd.DataFrame(
        [
            order("o1", purchase="2018-03-01 10:00:00"),
            order(
                "o2",
                purchase="2018-03-03 10:00:00",
                status="canceled",
                carrier=None,
                delivered=None,
            ),
            order("old", purchase="2017-12-30 10:00:00"),
        ],
        columns=COLUMNS,
    )
    items = pd.DataFrame(
        {
            "order_id": ["o1", "o2", "old"],
            "order_item_id": ["1", "1", "1"],
            "product_id": ["p1", "p1", "p9"],
            "seller_id": ["s1", "s2", "s9"],
            "shipping_limit_date": ["2018-03-05 00:00:00"] * 3,
            "price": ["10.0", "20.0", "5.0"],
            "freight_value": ["1.0", "2.0", "0.5"],
        }
    )
    payments = pd.DataFrame(
        {
            "order_id": ["o1", "o2"],
            "payment_sequential": ["1", "1"],
            "payment_type": ["boleto", "voucher"],
            "payment_installments": ["1", "1"],
            "payment_value": ["11.0", "22.0"],
        }
    )
    reviews = pd.DataFrame(
        {
            "review_id": ["r1", "r1", "r2"],
            "order_id": ["o1", "o2", "o1"],
            "review_score": ["5", "4", "1"],
            "review_comment_title": [None, None, None],
            "review_comment_message": [None, "ok", None],
            # r1/o2 is dated before o2's purchase: it must not arrive before the order.
            "review_creation_date": [
                "2018-03-11 00:00:00",
                "2018-03-02 00:00:00",
                "2019-02-01 00:00:00",
            ],
            "review_answer_timestamp": ["2018-03-12 00:00:00"] * 3,
        }
    )
    customers = pd.DataFrame(
        {
            "customer_id": ["c-o1", "c-o2", "c-old"],
            "customer_unique_id": ["u1", "u1", "u2"],
            "customer_zip_code_prefix": ["01001", "01001", "02002"],
            "customer_city": ["sao paulo"] * 3,
            "customer_state": ["SP"] * 3,
        }
    )
    sellers = pd.DataFrame(
        {
            "seller_id": ["s1", "s2", "s9"],
            "seller_zip_code_prefix": ["03003"] * 3,
            "seller_city": ["x"] * 3,
            "seller_state": ["SP"] * 3,
        }
    )
    products = pd.DataFrame(
        {"product_id": ["p1", "p9"], "product_category_name": ["beleza_saude", None]}
    )
    translation = pd.DataFrame(
        {
            "product_category_name": ["beleza_saude"],
            "product_category_name_english": ["health_beauty"],
        }
    )
    geolocation = pd.DataFrame(
        {
            "geolocation_zip_code_prefix": ["01001", "01001", "01001"],
            "geolocation_lat": ["-23.5", "-23.7", "-23.6"],
            "geolocation_lng": ["-46.6", "-46.8", "-46.7"],
            "geolocation_city": ["sao paulo", "são paulo", "sao paulo"],
            "geolocation_state": ["SP", "SP", "SP"],
        }
    )
    return {
        "orders": orders.astype("str"),
        "order_items": items,
        "order_payments": payments,
        "order_reviews": reviews,
        "customers": customers,
        "sellers": sellers,
        "products": products,
        "product_category_name_translation": translation,
        "geolocation": geolocation,
    }


@pytest.fixture
def plan() -> ReplayPlan:
    return ReplayPlan(tables(), date(2018, 1, 1), date(2018, 12, 31))


def ids(frame: pd.DataFrame, column: str) -> list[str]:
    return sorted(frame[column])


def test_items_payments_customers_arrive_on_purchase_day(plan: ReplayPlan) -> None:
    day = date(2018, 3, 1)
    assert ids(plan.drop("order_items", day), "order_id") == ["o1"]
    assert ids(plan.drop("order_payments", day), "order_id") == ["o1"]
    assert ids(plan.drop("customers", day), "customer_id") == ["c-o1"]


def test_orders_outside_window_are_never_replayed(plan: ReplayPlan) -> None:
    every_day = pd.concat([plan.drop("order_items", d) for d in plan.days()])
    assert "old" not in set(every_day["order_id"])


def test_reviews_arrive_on_creation_day_but_never_before_their_order(plan: ReplayPlan) -> None:
    assert ids(plan.drop("order_reviews", date(2018, 3, 11)), "order_id") == ["o1"]
    assert ids(plan.drop("order_reviews", date(2018, 3, 3)), "order_id") == ["o2"]
    assert plan.drop("order_reviews", date(2018, 3, 2)).empty


def test_reviews_after_window_end_never_arrive() -> None:
    short = ReplayPlan(tables(), date(2018, 1, 1), date(2018, 6, 30))
    every_day = pd.concat([short.drop("order_reviews", d) for d in short.days()])
    assert ids(every_day, "review_id") == ["r1", "r1"]


def test_entities_arrive_on_first_reference(plan: ReplayPlan) -> None:
    assert ids(plan.drop("sellers", date(2018, 3, 1)), "seller_id") == ["s1"]
    assert ids(plan.drop("sellers", date(2018, 3, 3)), "seller_id") == ["s2"]
    assert ids(plan.drop("products", date(2018, 3, 1)), "product_id") == ["p1"]
    assert plan.drop("products", date(2018, 3, 3)).empty


def test_drop_keeps_source_columns_only(plan: ReplayPlan) -> None:
    drop = plan.drop("orders", date(2018, 3, 1))
    assert list(drop.columns) == COLUMNS


def test_empty_day_has_empty_drops(plan: ReplayPlan) -> None:
    assert plan.drop("orders", date(2018, 1, 2)).empty
    assert len(plan.days()) == 365


def test_reference_tables_are_not_daily(plan: ReplayPlan) -> None:
    with pytest.raises(ValueError, match="not a daily table"):
        plan.drop("geolocation", date(2018, 3, 1))
    assert len(plan.reference("product_category_name_translation")) == 1


def test_geolocation_dedupe_uses_median_and_most_frequent_name() -> None:
    deduped = dedupe_geolocation(tables()["geolocation"])
    assert len(deduped) == 1
    row = deduped.iloc[0]
    assert row["geolocation_lat"] == "-23.600000"
    assert row["geolocation_lng"] == "-46.700000"
    assert row["geolocation_city"] == "sao paulo"
