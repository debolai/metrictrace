"""Independent oracle: catalog metrics computed with plain pandas from the source CSVs.

This file must not import any MetricTrace code. It re-implements the documented replay
rules (in a deliberately different, row-by-row style) so that a bug in the pipeline cannot
pass by agreeing with itself. Never edit it to match pipeline output.

Metric definitions (spec v2, Phase 4; decision 0003 for "cancelled"):

* scope as of day A: orders purchased from the window start to A, in their state on A;
* cancelled: final-or-current status ``canceled`` or ``unavailable``;
* gmv / revenue_incl_freight: item price (plus freight) on non-cancelled orders, in BRL,
  and in USD at the purchase-day rate (latest published rate on or before that day);
* payments_collected: payment value on non-cancelled orders;
* orders: all orders; aov: gmv / non-cancelled orders; cancellation_rate: cancelled / orders;
* on_time_delivery_rate: delivered orders (with a delivery date known by A) delivered on or
  before the estimated date (dates compared), over those orders;
* avg_delivery_days: mean of delivery date minus purchase date, in days, same orders;
* avg_review_score, negative_review_rate (score 1-2): reviews that have arrived by A;
* active_sellers: distinct sellers with an item on a non-cancelled order;
* returning_customer_rate: distinct people (customer_unique_id) whose order in the group is
  not their first order in the replay, over distinct people in the group.

Usage:
    python -m tests.oracle.compute_golden                 # fixture -> data/fixtures/golden.json
    python -m tests.oracle.compute_golden --source DIR --fx-dir DIR --start 2017-01-01 \
        --end 2017-06-30 --as-of 2017-06-30 --out data/fixtures/golden_aws.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "data" / "fixtures"
FORMAT = "%Y-%m-%d %H:%M:%S"
CANCELLED = {"canceled", "unavailable"}
MILESTONES = [
    ("order_purchase_timestamp", "created"),
    ("order_approved_at", "approved"),
    ("order_delivered_carrier_date", "shipped"),
    ("order_delivered_customer_date", "delivered"),
]
FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
}


def _day(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    if isinstance(value, str) and not value:
        return None
    return datetime.strptime(str(value), FORMAT).date()


def order_state(row: pd.Series, as_of: date) -> dict[str, Any]:
    """An order's status and visible delivery date on ``as_of`` (row-by-row rules)."""
    purchase = _day(row["order_purchase_timestamp"])
    assert purchase is not None
    days: list[date | None] = []
    for column, _ in MILESTONES:
        day = _day(row[column])
        days.append(None if day is None else max(day, purchase))
    known = [day for day in days if day is not None]
    last = max(known)
    if last <= as_of:
        status = row["order_status"]
    else:
        reached = [i for i, day in enumerate(days) if day is not None and day <= as_of]
        status = MILESTONES[max(reached)][1]
    delivered_day = days[3] if days[3] is not None and days[3] <= as_of else None
    raw_delivered = _day(row["order_delivered_customer_date"])
    return {
        "status": status,
        "delivered_raw_day": raw_delivered if delivered_day is not None else None,
    }


def usd_rates(fx_payloads: list[dict[str, Any]]) -> pd.Series:
    rates: dict[date, float] = {}
    for payload in fx_payloads:
        for day, values in payload["rates"].items():
            rates[date.fromisoformat(day)] = float(values["USD"])
    return pd.Series(rates).sort_index()


def usd_rate_on(rates: pd.Series, day: date) -> float:
    eligible = rates[rates.index <= day]
    if eligible.empty:
        raise ValueError(f"no FX rate on or before {day}")
    return float(eligible.iloc[-1])


def compute(
    source: Path, fx_payloads: list[dict[str, Any]], start: date, as_of: date
) -> dict[str, Any]:
    raw = {
        name: pd.read_csv(source / file, dtype=str, keep_default_na=False, na_values=[""])
        for name, file in FILES.items()
    }
    orders = raw["orders"].copy()
    orders["purchase_day"] = orders["order_purchase_timestamp"].map(_day)
    orders = orders[(orders["purchase_day"] >= start) & (orders["purchase_day"] <= as_of)]
    states = orders.apply(lambda row: pd.Series(order_state(row, as_of)), axis=1)
    orders = pd.concat([orders, states], axis=1)
    orders["cancelled"] = orders["status"].isin(CANCELLED)
    orders["month"] = orders["purchase_day"].map(lambda d: d.strftime("%Y-%m"))
    orders["estimated_day"] = orders["order_estimated_delivery_date"].map(_day)

    person = raw["customers"].set_index("customer_id")["customer_unique_id"]
    orders["person"] = orders["customer_id"].map(person)
    ranked = orders.sort_values(["person", "order_purchase_timestamp", "order_id"])
    first_ids = set(ranked.drop_duplicates("person")["order_id"])
    orders["is_first"] = orders["order_id"].isin(first_ids)

    rates = usd_rates(fx_payloads)
    items = raw["items"].merge(
        orders[["order_id", "cancelled", "month", "purchase_day"]], on="order_id"
    )
    items["price"] = items["price"].astype(float)
    items["freight_value"] = items["freight_value"].astype(float)
    items["usd"] = items["purchase_day"].map(lambda d: usd_rate_on(rates, d))

    payments = raw["payments"].merge(orders[["order_id", "cancelled", "month"]], on="order_id")
    payments["payment_value"] = payments["payment_value"].astype(float)

    reviews = raw["reviews"].merge(orders[["order_id", "purchase_day", "month"]], on="order_id")
    reviews["arrival"] = [
        max(_day(created) or purchase, purchase)
        for created, purchase in zip(
            reviews["review_creation_date"], reviews["purchase_day"], strict=True
        )
    ]
    reviews = reviews[reviews["arrival"] <= as_of]
    reviews["score"] = reviews["review_score"].astype(int)

    def metrics(
        o: pd.DataFrame, i: pd.DataFrame, p: pd.DataFrame, r: pd.DataFrame
    ) -> dict[str, Any]:
        live = o[~o["cancelled"]]
        live_items = i[~i["cancelled"]]
        delivered = o[(o["status"] == "delivered") & o["delivered_raw_day"].notna()]
        delivery_days = [
            (d - pday).days
            for d, pday in zip(
                delivered["delivered_raw_day"], delivered["purchase_day"], strict=True
            )
        ]
        on_time = sum(
            d <= est
            for d, est in zip(
                delivered["delivered_raw_day"], delivered["estimated_day"], strict=True
            )
        )
        gmv = float(live_items["price"].sum())
        people = o["person"].nunique()
        returning = o.loc[~o["is_first"], "person"].nunique()
        return {
            "orders": len(o),
            "gmv": gmv,
            "gmv_usd": float((live_items["price"] * live_items["usd"]).sum()),
            "revenue_incl_freight": float(
                (live_items["price"] + live_items["freight_value"]).sum()
            ),
            "revenue_incl_freight_usd": float(
                ((live_items["price"] + live_items["freight_value"]) * live_items["usd"]).sum()
            ),
            "payments_collected": float(p.loc[~p["cancelled"], "payment_value"].sum()),
            "aov": gmv / len(live) if len(live) else None,
            "cancellation_rate": float(o["cancelled"].mean()) if len(o) else None,
            "on_time_delivery_rate": on_time / len(delivered) if len(delivered) else None,
            "avg_delivery_days": sum(delivery_days) / len(delivery_days) if delivery_days else None,
            "avg_review_score": float(r["score"].mean()) if len(r) else None,
            "negative_review_rate": float((r["score"] <= 2).mean()) if len(r) else None,
            "active_sellers": int(live_items["seller_id"].nunique()),
            "returning_customer_rate": returning / people if people else None,
        }

    by_month = {
        month: metrics(
            orders[orders["month"] == month],
            items[items["month"] == month],
            payments[payments["month"] == month],
            reviews[reviews["month"] == month],
        )
        for month in sorted(orders["month"].unique())
    }
    return {"overall": metrics(orders, items, payments, reviews), "by_month": by_month}


def fx_payloads_from(fx_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text()) for path in sorted(fx_dir.glob("*.json"))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=FIXTURES / "olist")
    parser.add_argument("--fx-dir", type=Path, default=FIXTURES / "api" / "frankfurter")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--as-of", dest="as_of", type=date.fromisoformat, action="append")
    parser.add_argument("--out", type=Path, default=FIXTURES / "golden.json")
    args = parser.parse_args()

    manifest = json.loads((FIXTURES / "fixture.json").read_text())
    start = args.start or date.fromisoformat(manifest["sim_start_date"])
    as_of_dates = args.as_of or [date.fromisoformat(d) for d in manifest["as_of_dates"]]
    payloads = fx_payloads_from(args.fx_dir)
    golden = {
        "window_start": start.isoformat(),
        "as_of": {
            day.isoformat(): compute(args.source, payloads, start, day) for day in as_of_dates
        },
    }
    args.out.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
