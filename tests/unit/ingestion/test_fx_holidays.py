"""FX and holiday parsing, against the responses recorded in data/fixtures/api."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from metrictrace.ingestion import fx_rates, holidays
from metrictrace.ingestion.contracts import apply_contract
from metrictrace.ingestion.http import CachedJsonClient

API_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "api"


@pytest.fixture
def fx_client() -> CachedJsonClient:
    return CachedJsonClient("frankfurter", "https://unused.test", API_DIR, offline=True)


def test_weekday_drop_has_one_row_per_currency(fx_client: CachedJsonClient) -> None:
    drop = fx_rates.fx_drop(fx_rates.fetch_year(fx_client, 2018), date(2018, 5, 15))
    assert list(drop["currency"]) == ["EUR", "USD"]
    assert set(drop["rate_date"]) == {"2018-05-15"}
    assert apply_contract("fx", drop).passed


def test_weekend_drop_is_empty(fx_client: CachedJsonClient) -> None:
    drop = fx_rates.fx_drop(fx_rates.fetch_year(fx_client, 2018), date(2018, 5, 12))
    assert drop.empty
    assert list(drop.columns) == fx_rates.COLUMNS


def test_holidays_map_to_contract_columns() -> None:
    nager = CachedJsonClient("nager_date", "https://unused.test", API_DIR, offline=True)
    frame = holidays.holidays_for_year(nager, 2018)
    result = apply_contract("holidays", frame)
    assert result.passed
    assert len(result.valid) == len(frame) > 10
    assert "Carnival" in set(frame["name"])
    assert frame["is_global"].dtype == bool
