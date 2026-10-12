"""Replay plan: which rows of each Olist table arrive on which simulated day.

Arrival rules (spec, Phases 1-2):

* orders: one version per event day (see :mod:`lifecycle`);
* order items, payments and customers: with the order's first version (purchase day);
* reviews: on ``review_creation_date``, or on the purchase day if the review is dated
  earlier (a real data quirk), so a review never arrives before its order;
* sellers and products: on the first day an order in the window references them;
* geolocation and the category translation: reference data, loaded once.

Only orders purchased inside the replay window are replayed, and nothing arrives after
the window's end. Every rule is a pure function of the source, so a day's drop is
identical every time it is generated.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from metrictrace.ingestion.lifecycle import TIMESTAMP_FORMAT, in_window, order_versions
from metrictrace.ingestion.olist_source import DAILY_TABLES

DROP_DATE = "drop_date"


class ReplayPlan:
    """Daily drops for every Olist table over the replay window."""

    def __init__(self, tables: dict[str, pd.DataFrame], start: date, end: date) -> None:
        self.start = start
        self.end = end
        self._columns = {name: list(frame.columns) for name, frame in tables.items()}
        self._reference = {
            "product_category_name_translation": tables["product_category_name_translation"],
            "geolocation": dedupe_geolocation(tables["geolocation"]),
        }

        orders = in_window(tables["orders"], start, end)
        purchased = pd.to_datetime(orders["order_purchase_timestamp"], format=TIMESTAMP_FORMAT)
        purchase_day = pd.Series(
            purchased.dt.normalize().to_numpy(), index=orders["order_id"].to_numpy()
        )

        dated: dict[str, pd.DataFrame] = {
            "orders": order_versions(tables["orders"], start, end),
            "order_items": _with_order_day(tables["order_items"], purchase_day),
            "order_payments": _with_order_day(tables["order_payments"], purchase_day),
            "customers": _with_order_day(
                tables["customers"],
                pd.Series(purchase_day.to_numpy(), index=orders["customer_id"].to_numpy()),
                key="customer_id",
            ),
            "order_reviews": _reviews(tables["order_reviews"], purchase_day),
        }
        items = dated["order_items"]
        dated["sellers"] = _first_reference(tables["sellers"], items, "seller_id")
        dated["products"] = _first_reference(tables["products"], items, "product_id")

        last_day = pd.Timestamp(end)
        self._dated = {
            name: frame[frame[DROP_DATE] <= last_day].reset_index(drop=True)
            for name, frame in dated.items()
        }
        self._by_day = {
            name: frame.groupby(DROP_DATE).indices for name, frame in self._dated.items()
        }

    def drop(self, table: str, day: date) -> pd.DataFrame:
        """Rows of ``table`` arriving on ``day``, with the source's columns only."""
        if table not in DAILY_TABLES:
            raise ValueError(f"{table!r} is not a daily table")
        frame = self._dated[table]
        positions = self._by_day[table].get(pd.Timestamp(day))
        rows = frame.iloc[positions] if positions is not None else frame.iloc[0:0]
        return rows[self._columns[table]].reset_index(drop=True)

    def reference(self, table: str) -> pd.DataFrame:
        """A reference table, as loaded once."""
        return self._reference[table]

    def days(self) -> list[date]:
        """Every day in the window, including days with no arrivals."""
        return [day.date() for day in pd.date_range(self.start, self.end, freq="D")]


def _with_order_day(
    frame: pd.DataFrame, order_day: pd.Series, key: str = "order_id"
) -> pd.DataFrame:
    """Rows whose ``key`` maps to an in-window order, dated with that order's day."""
    day = frame[key].map(order_day)
    return frame.assign(**{DROP_DATE: day}).dropna(subset=[DROP_DATE])


def _reviews(reviews: pd.DataFrame, purchase_day: pd.Series) -> pd.DataFrame:
    created = pd.to_datetime(reviews["review_creation_date"], format=TIMESTAMP_FORMAT)
    order_day = reviews["order_id"].map(purchase_day)
    day = created.dt.normalize().where(created.dt.normalize() >= order_day, order_day)
    return reviews.assign(**{DROP_DATE: day.where(order_day.notna())}).dropna(subset=[DROP_DATE])


def _first_reference(entities: pd.DataFrame, items: pd.DataFrame, key: str) -> pd.DataFrame:
    first_day = items.groupby(key)[DROP_DATE].min()
    return _with_order_day(entities, first_day, key=key)


def dedupe_geolocation(geolocation: pd.DataFrame) -> pd.DataFrame:
    """One row per zip prefix: median coordinates, most frequent city and state.

    Ties between equally frequent names go to the alphabetically first, so the
    result is deterministic.
    """
    zip_col, lat, lng = (
        "geolocation_zip_code_prefix",
        "geolocation_lat",
        "geolocation_lng",
    )
    coords = (
        geolocation.assign(
            **{lat: geolocation[lat].astype(float), lng: geolocation[lng].astype(float)}
        )
        .groupby(zip_col)[[lat, lng]]
        .median()
    )

    def most_frequent(column: str) -> pd.Series:
        counts = geolocation.groupby([zip_col, column]).size().rename("n").reset_index()
        counts = counts.sort_values([zip_col, "n", column], ascending=[True, False, True])
        return counts.drop_duplicates(zip_col).set_index(zip_col)[column]

    result = coords.join(most_frequent("geolocation_city")).join(most_frequent("geolocation_state"))
    result[lat] = result[lat].map(lambda value: f"{value:.6f}")
    result[lng] = result[lng].map(lambda value: f"{value:.6f}")
    result = result.reset_index()
    columns = [zip_col, lat, lng, "geolocation_city", "geolocation_state"]
    return result[columns].astype("str").sort_values(zip_col).reset_index(drop=True)
