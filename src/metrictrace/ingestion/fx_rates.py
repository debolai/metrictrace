"""BRL exchange rates from Frankfurter (ECB reference rates).

Rates are fetched a calendar year at a time and cached; the drop for a day holds the
rates published for that day. The ECB publishes no rates on weekends and TARGET
holidays, so those days have empty drops, and dbt forward-fills them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from metrictrace.ingestion.http import CachedJsonClient

BASE = "BRL"
CURRENCIES = ("EUR", "USD")
COLUMNS = ["rate_date", "base", "currency", "rate"]


def fetch_year(client: CachedJsonClient, year: int) -> dict[str, Any]:
    payload: dict[str, Any] = client.get_json(
        f"{year}-01-01..{year}-12-31", {"from": BASE, "to": ",".join(CURRENCIES)}
    )
    return payload


def fx_drop(payload: dict[str, Any], day: date) -> pd.DataFrame:
    """Rates published for ``day``, one row per currency, as strings."""
    rates: dict[str, float] = payload.get("rates", {}).get(day.isoformat(), {})
    rows = [
        {"rate_date": day.isoformat(), "base": payload["base"], "currency": c, "rate": str(r)}
        for c, r in sorted(rates.items())
    ]
    return pd.DataFrame(rows, columns=COLUMNS, dtype="str")
