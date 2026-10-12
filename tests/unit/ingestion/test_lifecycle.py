from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from metrictrace.ingestion.lifecycle import MILESTONE_COLUMNS, order_versions

START, END = date(2018, 1, 1), date(2018, 12, 31)
COLUMNS = [
    "order_id",
    "customer_id",
    "order_status",
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]


def order(
    order_id: str = "o1",
    *,
    status: str = "delivered",
    purchase: str = "2018-03-01 10:00:00",
    approved: str | None = "2018-03-02 09:00:00",
    carrier: str | None = "2018-03-05 12:00:00",
    delivered: str | None = "2018-03-10 15:00:00",
    estimated: str = "2018-03-20 00:00:00",
) -> dict[str, str | None]:
    return dict(
        zip(
            COLUMNS,
            [order_id, f"c-{order_id}", status, purchase, approved, carrier, delivered, estimated],
            strict=True,
        )
    )


def versions(*rows: dict[str, str | None], end: date = END) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows), columns=COLUMNS).astype("str")
    return order_versions(frame, START, end)


def days(frame: pd.DataFrame) -> list[str]:
    return [d.date().isoformat() for d in frame["drop_date"]]


def test_delivered_order_gets_one_version_per_milestone() -> None:
    v = versions(order())
    assert days(v) == ["2018-03-01", "2018-03-02", "2018-03-05", "2018-03-10"]
    assert list(v["order_status"]) == ["created", "approved", "shipped", "delivered"]
    assert v["order_approved_at"].isna().tolist() == [True, False, False, False]
    assert v["order_delivered_customer_date"].notna().tolist() == [False, False, False, True]
    assert v["order_estimated_delivery_date"].notna().all()


def test_same_day_approval_starts_as_approved() -> None:
    v = versions(order(approved="2018-03-01 11:00:00"))
    assert days(v)[0] == "2018-03-01"
    assert v["order_status"].iloc[0] == "approved"


def test_same_day_events_collapse_into_end_of_day_state() -> None:
    v = versions(order(carrier="2018-03-02 18:00:00"))
    assert days(v) == ["2018-03-01", "2018-03-02", "2018-03-10"]
    assert v["order_status"].iloc[1] == "shipped"
    assert v["order_approved_at"].iloc[1] == "2018-03-02 09:00:00"


def test_final_status_applies_on_last_event_day() -> None:
    v = versions(order(status="canceled", carrier=None, delivered=None))
    assert list(v["order_status"]) == ["created", "canceled"]


def test_shipped_final_status() -> None:
    v = versions(order(status="shipped", delivered=None))
    assert list(v["order_status"]) == ["created", "approved", "shipped"]


def test_created_and_approved_final_statuses() -> None:
    created = versions(order(status="created", approved=None, carrier=None, delivered=None))
    assert list(created["order_status"]) == ["created"]
    approved = versions(order(status="approved", carrier=None, delivered=None))
    assert list(approved["order_status"]) == ["created", "approved"]


def test_delivered_without_delivery_date_finishes_on_last_known_day() -> None:
    v = versions(order(delivered=None))
    assert days(v)[-1] == "2018-03-05"
    assert v["order_status"].iloc[-1] == "delivered"
    assert v["order_delivered_customer_date"].isna().all()


def test_milestone_before_purchase_is_clamped_to_purchase_day() -> None:
    v = versions(order(approved="2018-02-27 08:00:00"))
    assert days(v)[0] == "2018-03-01"
    assert v["order_status"].iloc[0] == "approved"
    assert v["order_approved_at"].iloc[0] == "2018-02-27 08:00:00"


def test_events_after_window_end_are_censored() -> None:
    v = versions(order(), end=date(2018, 3, 7))
    assert days(v) == ["2018-03-01", "2018-03-02", "2018-03-05"]
    assert v["order_status"].iloc[-1] == "shipped"
    assert v["order_delivered_customer_date"].isna().all()


def test_orders_outside_window_are_excluded() -> None:
    v = versions(
        order("early", purchase="2017-12-31 23:00:00"),
        order("late", purchase="2019-01-01 00:00:00"),
    )
    assert v.empty
    assert "drop_date" in v.columns


def test_output_is_sorted_by_day_then_order() -> None:
    v = versions(order("b"), order("a", purchase="2018-03-01 08:00:00"))
    assert list(v["order_id"][:2]) == ["a", "b"]
    assert v["drop_date"].is_monotonic_increasing


timestamps = st.datetimes(min_value=datetime(2018, 1, 1), max_value=datetime(2018, 12, 31))
statuses = st.sampled_from(
    [
        "created",
        "approved",
        "invoiced",
        "processing",
        "shipped",
        "delivered",
        "canceled",
        "unavailable",
    ]
)


@st.composite
def random_order(draw: st.DrawFn, n: int) -> dict[str, str | None]:
    purchase = draw(timestamps)
    offsets = [draw(st.none() | st.integers(min_value=-2, max_value=40)) for _ in range(3)]
    stamps = [
        None if o is None else (purchase + timedelta(days=o, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        for o in offsets
    ]
    return order(
        order_id=f"o{n}",
        status=draw(statuses),
        purchase=purchase.strftime("%Y-%m-%d %H:%M:%S"),
        approved=stamps[0],
        carrier=stamps[1],
        delivered=stamps[2],
    )


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.integers(), min_size=1, max_size=8).flatmap(
        lambda ns: st.tuples(*[random_order(i) for i in range(len(ns))])
    ),
    st.dates(min_value=date(2018, 1, 1), max_value=date(2018, 12, 31)),
)
def test_version_invariants(orders: tuple[dict[str, str | None], ...], end: date) -> None:
    v = versions(*orders, end=end)
    assert not v.duplicated(["order_id", "drop_date"]).any()
    assert (v["drop_date"] <= pd.Timestamp(end)).all()
    purchase_day = pd.to_datetime(v["order_purchase_timestamp"]).dt.normalize()
    assert (v["drop_date"] >= purchase_day).all()
    for _, history in v.groupby("order_id"):
        # Once a milestone is shown it stays shown in every later version.
        for column in MILESTONE_COLUMNS:
            shown = history[column].notna().tolist()
            assert shown == sorted(shown)
    final = {o["order_id"]: o["order_status"] for o in orders}
    last = v.drop_duplicates("order_id", keep="last")
    for _, row in last.iterrows():
        source = next(o for o in orders if o["order_id"] == row["order_id"])
        stamps = [source[c] for c in MILESTONE_COLUMNS if source[c] is not None]
        purchase_d = datetime.strptime(
            str(source["order_purchase_timestamp"]), "%Y-%m-%d %H:%M:%S"
        ).date()
        last_day = max(
            max(datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").date(), purchase_d) for s in stamps
        )
        if last_day <= end:
            assert row["order_status"] == final[row["order_id"]]
