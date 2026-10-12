"""Order lifecycle reconstruction.

Olist stores each order once, in its final state, with milestone timestamps. To replay it
as a live source, each order becomes a sequence of versions, one per simulated day on which
something happened to it. A version is the order's end-of-day state on that day:

* status: the furthest milestone reached by that day (created, approved, shipped,
  delivered); on the order's last event day, its final recorded status instead;
* milestone timestamps: shown only once their day has been reached;
* purchase timestamp, estimated delivery date and customer: always shown.

Milestones dated before the purchase (a real data quirk) are treated as happening on the
purchase day. Events after the replay window's end are never emitted, so orders still
open at the end stay in their last emitted state.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

PURCHASE = "order_purchase_timestamp"
# Milestones in lifecycle order, with the status an order has once it reaches each one.
MILESTONES: tuple[tuple[str, str], ...] = (
    (PURCHASE, "created"),
    ("order_approved_at", "approved"),
    ("order_delivered_carrier_date", "shipped"),
    ("order_delivered_customer_date", "delivered"),
)
MILESTONE_COLUMNS = tuple(column for column, _ in MILESTONES)
MILESTONE_STATUSES = np.array([status for _, status in MILESTONES], dtype=object)


def purchase_dates(orders: pd.DataFrame) -> pd.Series:
    """Calendar day of each order's purchase."""
    return pd.to_datetime(orders[PURCHASE], format=TIMESTAMP_FORMAT).dt.normalize()


def in_window(orders: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Orders purchased within the replay window."""
    purchased = purchase_dates(orders)
    return orders[(purchased >= pd.Timestamp(start)) & (purchased <= pd.Timestamp(end))]


def milestone_days(orders: pd.DataFrame) -> pd.DataFrame:
    """Day each milestone is reached, clamped so nothing happens before the purchase."""
    purchased = purchase_dates(orders)
    days = {}
    for column in MILESTONE_COLUMNS:
        day = pd.to_datetime(orders[column], format=TIMESTAMP_FORMAT).dt.normalize()
        days[column] = day.where(day.isna() | (day >= purchased), purchased)
    return pd.DataFrame(days, index=orders.index)


def order_versions(orders: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """One row per order per event day within the window, holding the end-of-day state.

    Input columns are the Olist orders columns as strings; the output has the same
    columns plus ``drop_date`` (a normalized timestamp), sorted by drop date and order.
    """
    orders = in_window(orders, start, end)
    if orders.empty:
        return orders.assign(drop_date=pd.Series(dtype="datetime64[us]"))

    days = milestone_days(orders)
    last_event = days.max(axis=1)

    long = days.reset_index(names="_row").melt(
        id_vars="_row", var_name="milestone", value_name="drop_date"
    )
    long = long.dropna(subset=["drop_date"])
    long = long[long["drop_date"] <= pd.Timestamp(end)]
    versions = long[["_row", "drop_date"]].drop_duplicates().sort_values(["_row", "drop_date"])

    rows = versions["_row"].to_numpy()
    drop = versions["drop_date"].to_numpy()
    reached = np.column_stack(
        [(days[column].loc[rows].to_numpy() <= drop) for column in MILESTONE_COLUMNS]
    )
    # Furthest milestone reached by the drop day (purchase is always reached).
    furthest = np.where(reached, np.arange(len(MILESTONE_COLUMNS)), 0).max(axis=1)
    is_last = drop == last_event.loc[rows].to_numpy()
    status = np.where(
        is_last, orders.loc[rows, "order_status"].to_numpy(), MILESTONE_STATUSES[furthest]
    )

    result = orders.loc[rows].copy()
    for position, column in enumerate(MILESTONE_COLUMNS):
        if column != PURCHASE:
            result[column] = result[column].where(reached[:, position])
    result["order_status"] = pd.Series(status, index=result.index, dtype="str")
    result["drop_date"] = drop
    result = result.sort_values(["drop_date", "order_id"], kind="stable")
    return result.reset_index(drop=True)
