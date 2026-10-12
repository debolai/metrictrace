"""Build the committed fixture dataset from the full Olist download (``make fixtures``).

The fixture is a seeded, deterministic sample of orders purchased in a fixed window,
with every row they reference, plus recorded FX and holiday API responses. Known data
quirks are forced into the sample so tests exercise them. Running this twice on the
same download produces identical files.

Needs the full download in OLIST_SOURCE_PATH and network access (to record the APIs).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from metrictrace.config import load_settings
from metrictrace.ingestion import fx_rates, holidays
from metrictrace.ingestion.http import CachedJsonClient
from metrictrace.ingestion.lifecycle import TIMESTAMP_FORMAT
from metrictrace.ingestion.olist_source import TABLES, load_source

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
SEED = 20180201
PURCHASE_START = date(2018, 2, 1)
PURCHASE_END = date(2018, 6, 30)
SIM_END = date(2018, 8, 29)  # 60-day tail so lifecycle events after the window land
AS_OF_DATES = (date(2018, 4, 30), SIM_END)  # mid-window (orders still open) and the end
TARGET_ORDERS = 2000
GEO_ROWS_PER_ZIP = 5
NON_DELIVERED_PER_STATUS = 4
REPEAT_CUSTOMERS = 15
SHARED_REVIEWS = 3


def _pick(rng: np.random.Generator, ids: pd.Series, n: int) -> set[str]:
    ordered = np.sort(ids.unique())
    if len(ordered) <= n:
        return set(ordered)
    return set(rng.choice(ordered, size=n, replace=False))


def select_orders(
    tables: dict[str, pd.DataFrame], rng: np.random.Generator
) -> tuple[set[str], dict[str, int]]:
    orders = tables["orders"]
    purchased = pd.to_datetime(orders["order_purchase_timestamp"], format=TIMESTAMP_FORMAT)
    window = orders[
        (purchased.dt.date >= PURCHASE_START) & (purchased.dt.date <= PURCHASE_END)
    ].sort_values("order_id")
    in_window = set(window["order_id"])
    quirks: dict[str, set[str]] = {}

    for status in ("created", "approved"):
        quirks[f"final_status_{status}"] = set(
            window.loc[window["order_status"] == status, "order_id"]
        )
    for status in ("shipped", "canceled", "unavailable", "invoiced", "processing"):
        matching = window.loc[window["order_status"] == status, "order_id"]
        quirks[f"final_status_{status}"] = _pick(rng, matching, NON_DELIVERED_PER_STATUS)
    quirks["delivered_without_date"] = set(
        window.loc[
            (window["order_status"] == "delivered")
            & window["order_delivered_customer_date"].isna(),
            "order_id",
        ]
    )

    raw_days = {
        column: pd.to_datetime(window[column], format=TIMESTAMP_FORMAT).dt.normalize()
        for column in ("order_approved_at", "order_delivered_carrier_date")
    }
    purchase_day = pd.to_datetime(
        window["order_purchase_timestamp"], format=TIMESTAMP_FORMAT
    ).dt.normalize()
    before = (raw_days["order_approved_at"] < purchase_day) | (
        raw_days["order_delivered_carrier_date"] < purchase_day
    )
    quirks["milestone_before_purchase"] = _pick(rng, window.loc[before, "order_id"], 3)

    items = tables["order_items"].merge(tables["products"], on="product_id", how="left")
    translated = set(tables["product_category_name_translation"]["product_category_name"])
    window_items = items[items["order_id"].isin(in_window)]
    untranslated = window_items["product_category_name"].notna() & ~window_items[
        "product_category_name"
    ].isin(translated)
    quirks["untranslated_category"] = set(window_items.loc[untranslated, "order_id"])
    quirks["null_category"] = _pick(
        rng, window_items.loc[window_items["product_category_name"].isna(), "order_id"], 3
    )

    reviews = tables["order_reviews"]
    created = pd.to_datetime(
        reviews["review_creation_date"], format=TIMESTAMP_FORMAT
    ).dt.normalize()
    review_purchase = reviews["order_id"].map(
        dict(zip(window["order_id"], purchase_day, strict=True))
    )
    quirks["review_before_purchase"] = _pick(
        rng, reviews.loc[review_purchase.notna() & (created < review_purchase), "order_id"], 3
    )
    shared = reviews.groupby("review_id")["order_id"].agg(set)
    shared = shared[shared.map(lambda ids: len(ids) > 1 and ids <= in_window)]
    chosen_reviews = _pick(rng, pd.Series(shared.index), SHARED_REVIEWS)
    quirks["shared_review_id"] = set().union(*(shared[r] for r in sorted(chosen_reviews)))

    customers = tables["customers"].set_index("customer_id")["customer_unique_id"]
    person = window["customer_id"].map(customers)
    all_orders_person = tables["orders"]["customer_id"].map(customers)
    counts = all_orders_person.value_counts()
    window_counts = person.value_counts()
    repeaters = window_counts[
        (window_counts >= 2) & (window_counts == counts.reindex(window_counts.index))
    ]
    chosen_people = _pick(rng, pd.Series(repeaters.index), REPEAT_CUSTOMERS)
    quirks["repeat_customer"] = set(window.loc[person.isin(chosen_people), "order_id"])

    selected: set[str] = set().union(*quirks.values())
    remaining = window.loc[~window["order_id"].isin(selected), "order_id"]
    selected |= _pick(rng, remaining, max(TARGET_ORDERS - len(selected), 0))
    return selected, {name: len(ids) for name, ids in sorted(quirks.items())}


def subset(tables: dict[str, pd.DataFrame], order_ids: set[str]) -> dict[str, pd.DataFrame]:
    orders = tables["orders"][tables["orders"]["order_id"].isin(order_ids)]
    items = tables["order_items"][tables["order_items"]["order_id"].isin(order_ids)]
    customers = tables["customers"][tables["customers"]["customer_id"].isin(orders["customer_id"])]
    sellers = tables["sellers"][tables["sellers"]["seller_id"].isin(items["seller_id"])]
    zips = set(customers["customer_zip_code_prefix"]) | set(sellers["seller_zip_code_prefix"])
    geo = tables["geolocation"]
    geo = (
        geo[geo["geolocation_zip_code_prefix"].isin(zips)]
        .sort_values(list(geo.columns))
        .groupby("geolocation_zip_code_prefix")
        .head(GEO_ROWS_PER_ZIP)
    )
    result = {
        "orders": orders,
        "order_items": items,
        "order_payments": tables["order_payments"][
            tables["order_payments"]["order_id"].isin(order_ids)
        ],
        "order_reviews": tables["order_reviews"][
            tables["order_reviews"]["order_id"].isin(order_ids)
        ],
        "customers": customers,
        "sellers": sellers,
        "products": tables["products"][tables["products"]["product_id"].isin(items["product_id"])],
        "product_category_name_translation": tables["product_category_name_translation"],
        "geolocation": geo,
    }
    key_sorted = {}
    for name, frame in result.items():
        key_sorted[name] = frame.sort_values(list(TABLES[name].key), kind="stable")
    return key_sorted


def record_apis() -> None:
    settings = load_settings()
    api_dir = FIXTURES / "api"
    fx = CachedJsonClient("frankfurter", settings.fx_api_base_url, api_dir, offline=False)
    nager = CachedJsonClient("nager_date", settings.holidays_api_base_url, api_dir, offline=False)
    for year in sorted({PURCHASE_START.year, SIM_END.year}):
        fx_rates.fetch_year(fx, year)
        holidays.holidays_for_year(nager, year)


def main() -> None:
    settings = load_settings()
    tables = load_source(settings.olist_source_path)
    rng = np.random.default_rng(SEED)
    order_ids, quirks = select_orders(tables, rng)
    fixture = subset(tables, order_ids)

    olist_dir = FIXTURES / "olist"
    olist_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in fixture.items():
        frame.to_csv(olist_dir / TABLES[name].file_name, index=False)
    record_apis()

    manifest = {
        "seed": SEED,
        "purchase_window": [PURCHASE_START.isoformat(), PURCHASE_END.isoformat()],
        "sim_start_date": PURCHASE_START.isoformat(),
        "sim_end_date": SIM_END.isoformat(),
        "as_of_dates": [day.isoformat() for day in AS_OF_DATES],
        "fx_outage_candidates": [["2018-05-11", "2018-05-18"], ["2018-06-07", "2018-06-14"]],
        "rows": {name: len(frame) for name, frame in fixture.items()},
        "quirk_orders": quirks,
        # Real quirks too rare to fall in any single window; unit tests cover their rules.
        "known_gaps": {
            "order_without_payment": (
                "The only Olist order without payments was purchased in 2016-09, "
                "before any replay window, so it never appears in a replay."
            ),
            "final_status_approved": "Only 2 orders end as 'approved', both purchased in 2017.",
            "milestone_before_purchase": (
                "Only 2 orders have a milestone on an earlier day than purchase "
                "(2018-07 and 2018-08); 164 more are same-day, earlier-hour."
            ),
        },
    }
    (FIXTURES / "fixture.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
