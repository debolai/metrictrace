"""Ingestion contracts: column types, nullability, allowed values and keys per raw table.

Drops arrive as strings. A contract types each column; rows that fail any check are
quarantined with the reason, and a drop is rejected outright when more than 5% of its
rows fail or its columns do not match. Product category is deliberately not a closed
set here: new upstream categories are legitimate, so dbt monitors them instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

MAX_FAILURE_RATE = 0.05
MAX_VALIDATION_PASSES = 3
QUARANTINE_REASON = "_quarantine_reason"

ORDER_STATUSES = (
    "created",
    "approved",
    "invoiced",
    "processing",
    "shipped",
    "delivered",
    "canceled",
    "unavailable",
)
PAYMENT_TYPES = ("credit_card", "boleto", "voucher", "debit_card", "not_defined")
BRAZIL_STATES = (
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
)  # fmt: skip
ZIP_PREFIX = r"^\d{5}$"

TIMESTAMP = "datetime64[ns]"  # pandera stores timestamps at nanosecond precision


def _text(*checks: pa.Check, nullable: bool = False) -> pa.Column:
    return pa.Column("str", checks=list(checks), nullable=nullable)


def _timestamp(nullable: bool = False) -> pa.Column:
    return pa.Column(TIMESTAMP, nullable=nullable)


def _number(*checks: pa.Check, nullable: bool = False) -> pa.Column:
    return pa.Column("float64", checks=list(checks), nullable=nullable)


def _integer(*checks: pa.Check) -> pa.Column:
    return pa.Column("int64", checks=list(checks))


def _state() -> pa.Column:
    return _text(pa.Check.isin(BRAZIL_STATES))


SCHEMAS: dict[str, dict[str, pa.Column]] = {
    "orders": {
        "order_id": _text(),
        "customer_id": _text(),
        "order_status": _text(pa.Check.isin(ORDER_STATUSES)),
        "order_purchase_timestamp": _timestamp(),
        "order_approved_at": _timestamp(nullable=True),
        "order_delivered_carrier_date": _timestamp(nullable=True),
        "order_delivered_customer_date": _timestamp(nullable=True),
        "order_estimated_delivery_date": _timestamp(),
    },
    "order_items": {
        "order_id": _text(),
        "order_item_id": _integer(pa.Check.ge(1)),
        "product_id": _text(),
        "seller_id": _text(),
        "shipping_limit_date": _timestamp(),
        "price": _number(pa.Check.gt(0)),
        "freight_value": _number(pa.Check.ge(0)),
    },
    "order_payments": {
        "order_id": _text(),
        "payment_sequential": _integer(pa.Check.ge(1)),
        "payment_type": _text(pa.Check.isin(PAYMENT_TYPES)),
        "payment_installments": _integer(pa.Check.ge(0)),
        "payment_value": _number(pa.Check.ge(0)),
    },
    "order_reviews": {
        "review_id": _text(),
        "order_id": _text(),
        "review_score": _integer(pa.Check.isin((1, 2, 3, 4, 5))),
        "review_comment_title": _text(nullable=True),
        "review_comment_message": _text(nullable=True),
        "review_creation_date": _timestamp(),
        "review_answer_timestamp": _timestamp(),
    },
    "customers": {
        "customer_id": _text(),
        "customer_unique_id": _text(),
        "customer_zip_code_prefix": _text(pa.Check.str_matches(ZIP_PREFIX)),
        "customer_city": _text(),
        "customer_state": _state(),
    },
    "sellers": {
        "seller_id": _text(),
        "seller_zip_code_prefix": _text(pa.Check.str_matches(ZIP_PREFIX)),
        "seller_city": _text(),
        "seller_state": _state(),
    },
    "products": {
        "product_id": _text(),
        "product_category_name": _text(nullable=True),
        # The misspelled "lenght" columns are the source's names; staging renames them.
        "product_name_lenght": _number(pa.Check.ge(0), nullable=True),
        "product_description_lenght": _number(pa.Check.ge(0), nullable=True),
        "product_photos_qty": _number(pa.Check.ge(0), nullable=True),
        "product_weight_g": _number(pa.Check.ge(0), nullable=True),
        "product_length_cm": _number(pa.Check.ge(0), nullable=True),
        "product_height_cm": _number(pa.Check.ge(0), nullable=True),
        "product_width_cm": _number(pa.Check.ge(0), nullable=True),
    },
    "product_category_name_translation": {
        "product_category_name": _text(),
        "product_category_name_english": _text(),
    },
    "geolocation": {
        "geolocation_zip_code_prefix": _text(pa.Check.str_matches(ZIP_PREFIX)),
        # Bounding box of Brazil; the source has a few points elsewhere.
        "geolocation_lat": _number(pa.Check.in_range(-34.0, 6.0)),
        "geolocation_lng": _number(pa.Check.in_range(-74.0, -34.0)),
        "geolocation_city": _text(),
        "geolocation_state": _state(),
    },
    "fx": {
        "rate_date": _timestamp(),
        "base": _text(pa.Check.equal_to("BRL")),
        "currency": _text(pa.Check.isin(("USD", "EUR"))),
        "rate": _number(pa.Check.gt(0)),
    },
    "holidays": {
        "holiday_date": _timestamp(),
        "local_name": _text(),
        "name": _text(),
        "country_code": _text(pa.Check.equal_to("BR")),
        "is_global": pa.Column("bool"),
        "is_fixed": pa.Column("bool"),
        "types": _text(),
    },
}

KEYS: dict[str, tuple[str, ...]] = {
    "orders": ("order_id",),
    "order_items": ("order_id", "order_item_id"),
    "order_payments": ("order_id", "payment_sequential"),
    "order_reviews": ("review_id", "order_id"),
    "customers": ("customer_id",),
    "sellers": ("seller_id",),
    "products": ("product_id",),
    "product_category_name_translation": ("product_category_name",),
    "geolocation": ("geolocation_zip_code_prefix",),
    "fx": ("rate_date", "currency"),
    "holidays": ("holiday_date", "name"),
}


@dataclass
class ContractResult:
    """Outcome of applying a contract to one drop."""

    valid: pd.DataFrame  # typed rows that passed
    quarantined: pd.DataFrame  # original rows that failed, with the reason
    quarantined_index: pd.Index  # positions of the quarantined rows in the input
    input_rows: int
    passed: bool
    reasons: dict[str, int] = field(default_factory=dict)
    schema_error: str | None = None

    @property
    def failure_rate(self) -> float:
        return len(self.quarantined) / self.input_rows if self.input_rows else 0.0


def schema_for(table: str) -> pa.DataFrameSchema:
    return pa.DataFrameSchema(SCHEMAS[table], strict=True, coerce=True, ordered=True)


def apply_contract(
    table: str, frame: pd.DataFrame, max_failure_rate: float = MAX_FAILURE_RATE
) -> ContractResult:
    """Validate a drop; split it into typed valid rows and quarantined rows."""
    schema = schema_for(table)
    frame = frame.reset_index(drop=True)
    expected = list(SCHEMAS[table])
    if list(frame.columns) != expected:
        error = f"columns {list(frame.columns)} do not match contract {expected}"
        return _reject_all(table, frame, f"schema: {error}", schema_error=error)

    reasons = pd.Series("", index=frame.index, dtype="str")
    duplicated = frame.duplicated(subset=list(KEYS[table]), keep="first")
    reasons[duplicated] = "duplicate_key"

    # A value that cannot be typed also makes that column's value checks fail without a
    # row index, so row-level failures are quarantined first and the rest re-validated.
    # Only failures that persist with no failing row are structural.
    remaining = frame.index[reasons == ""]
    for _ in range(MAX_VALIDATION_PASSES):
        try:
            schema.validate(frame.loc[remaining], lazy=True)
            break
        except SchemaErrors as errors:
            cases = errors.failure_cases
            row_cases = cases[cases["index"].notna()]
            if row_cases.empty:
                error = _describe_cases(cases)
                return _reject_all(table, frame, f"schema: {error}", schema_error=error)
            labels = row_cases["column"].astype(str) + ":" + row_cases["check"].astype(str)
            per_row = labels.groupby(row_cases["index"].astype(int)).agg(
                lambda values: ",".join(sorted(set(values)))
            )
            for row, label in per_row.items():
                reasons.at[row] = str(label)
            remaining = remaining.difference(per_row.index)
    else:
        error = "validation did not converge"
        return _reject_all(table, frame, f"schema: {error}", schema_error=error)

    failed = reasons != ""
    quarantined = frame[failed].assign(**{QUARANTINE_REASON: reasons[failed]})
    counts = {str(k): int(v) for k, v in reasons[failed].value_counts().sort_index().items()}
    if len(frame) and failed.sum() / len(frame) > max_failure_rate:
        rejected = frame.assign(**{QUARANTINE_REASON: reasons.where(failed, "drop_rejected")})
        return ContractResult(
            valid=_empty(table),
            quarantined=rejected,
            quarantined_index=frame.index,
            input_rows=len(frame),
            passed=False,
            reasons=counts,
        )
    valid = schema.validate(frame[~failed]) if (~failed).any() else _empty(table)
    return ContractResult(
        valid=valid.reset_index(drop=True),
        quarantined=quarantined.reset_index(drop=True),
        quarantined_index=frame.index[failed],
        input_rows=len(frame),
        passed=True,
        reasons=counts,
    )


def _describe_cases(cases: pd.DataFrame) -> str:
    labels = {
        f"{column}:{check}" if isinstance(column, str) else str(check)
        for column, check in zip(cases["column"], cases["check"], strict=True)
    }
    return "; ".join(sorted(labels))


def _empty(table: str) -> pd.DataFrame:
    return pd.DataFrame(
        {name: pd.Series(dtype=column.dtype.type) for name, column in SCHEMAS[table].items()}
    )


def _reject_all(table: str, frame: pd.DataFrame, reason: str, schema_error: str) -> ContractResult:
    return ContractResult(
        valid=_empty(table),
        quarantined=frame.assign(**{QUARANTINE_REASON: reason}),
        quarantined_index=frame.index,
        input_rows=len(frame),
        passed=False,
        reasons={reason: len(frame)},
        schema_error=schema_error,
    )
