"""Brazilian public holidays from Nager.Date, one list per year."""

from __future__ import annotations

import pandas as pd

from metrictrace.ingestion.http import CachedJsonClient

COUNTRY = "BR"
COLUMNS = ["holiday_date", "local_name", "name", "country_code", "is_global", "is_fixed", "types"]


def holidays_for_year(client: CachedJsonClient, year: int) -> pd.DataFrame:
    payload = client.get_json(f"api/v3/PublicHolidays/{year}/{COUNTRY}")
    rows = [
        {
            "holiday_date": item["date"],
            "local_name": item["localName"],
            "name": item["name"],
            "country_code": item["countryCode"],
            "is_global": bool(item["global"]),
            "is_fixed": bool(item["fixed"]),
            "types": ",".join(item.get("types") or []),
        }
        for item in payload
    ]
    return pd.DataFrame(rows, columns=COLUMNS)
