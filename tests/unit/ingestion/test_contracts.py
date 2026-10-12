from __future__ import annotations

import pandas as pd

from metrictrace.ingestion.contracts import QUARANTINE_REASON, apply_contract
from tests.unit.ingestion.test_lifecycle import COLUMNS, order


def orders(*rows: dict[str, str | None]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=COLUMNS).astype("str")


def many(n: int) -> list[dict[str, str | None]]:
    return [order(f"o{i}") for i in range(n)]


def test_valid_rows_are_typed() -> None:
    result = apply_contract("orders", orders(*many(3)))
    assert result.passed
    assert result.quarantined.empty
    assert str(result.valid["order_purchase_timestamp"].dtype).startswith("datetime64")
    assert len(result.valid) == 3


def test_bad_value_is_quarantined_with_reason() -> None:
    rows = [*many(30), order("bad", status="lost")]
    result = apply_contract("orders", orders(*rows))
    assert result.passed
    assert len(result.valid) == 30
    assert list(result.quarantined["order_id"]) == ["bad"]
    assert "order_status:isin" in result.quarantined[QUARANTINE_REASON].iloc[0]
    assert list(result.quarantined_index) == [30]


def test_unparseable_timestamp_is_quarantined() -> None:
    rows = [*many(30), order("bad", purchase="not-a-date")]
    result = apply_contract("orders", orders(*rows))
    assert list(result.quarantined["order_id"]) == ["bad"]
    assert "order_purchase_timestamp" in result.quarantined[QUARANTINE_REASON].iloc[0]


def test_duplicate_key_keeps_first_and_quarantines_rest() -> None:
    rows = [*many(30), order("o1")]
    result = apply_contract("orders", orders(*rows))
    assert result.reasons == {"duplicate_key": 1}
    assert len(result.valid) == 30


def test_more_than_five_percent_failures_rejects_the_drop() -> None:
    rows = [*many(10), order("x", status="lost")]
    result = apply_contract("orders", orders(*rows))
    assert not result.passed
    assert result.valid.empty
    assert len(result.quarantined) == 11
    reasons = result.quarantined.set_index("order_id")[QUARANTINE_REASON]
    assert reasons["x"].startswith("order_status:isin")
    assert (reasons.drop("x") == "drop_rejected").all()


def test_schema_mismatch_rejects_the_drop() -> None:
    frame = orders(*many(2)).rename(columns={"order_status": "status"})
    result = apply_contract("orders", frame)
    assert not result.passed
    assert result.schema_error is not None
    assert result.quarantined[QUARANTINE_REASON].str.startswith("schema:").all()


def test_empty_drop_passes_with_typed_columns() -> None:
    result = apply_contract("orders", orders())
    assert result.passed
    assert result.failure_rate == 0.0
    assert list(result.valid.columns) == COLUMNS


def test_zip_prefix_and_state_checks() -> None:
    frame = pd.DataFrame(
        {
            "seller_id": ["s1", "s2"],
            "seller_zip_code_prefix": ["01001", "1001"],
            "seller_city": ["x", "y"],
            "seller_state": ["SP", "XX"],
        }
    )
    result = apply_contract("sellers", frame, max_failure_rate=1.0)
    assert list(result.quarantined["seller_id"]) == ["s2"]
    reason = result.quarantined[QUARANTINE_REASON].iloc[0]
    assert "seller_zip_code_prefix" in reason
    assert "seller_state" in reason


def test_one_untypeable_number_quarantines_only_that_row() -> None:
    # Regression: a failed cast also fails the column's value checks without a row index,
    # which used to reject the whole drop.
    frame = pd.DataFrame(
        {
            "order_id": [f"o{i}" for i in range(30)],
            "order_item_id": ["1"] * 30,
            "product_id": ["p"] * 30,
            "seller_id": ["s"] * 30,
            "shipping_limit_date": ["2018-03-05 00:00:00"] * 30,
            "price": ["10.0"] * 29 + ["abc"],
            "freight_value": ["1.0"] * 30,
        }
    )
    result = apply_contract("order_items", frame)
    assert result.passed
    assert len(result.valid) == 29
    assert list(result.quarantined["order_id"]) == ["o29"]
    assert result.quarantined[QUARANTINE_REASON].iloc[0].startswith("price:coerce_dtype")
