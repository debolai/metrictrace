"""Landing a drop in the lake: lineage, contract, partition overwrite and ``_meta`` record.

Each drop overwrites only its own partition, so re-running a date is idempotent. The
``_meta`` checksum covers the business columns (through ``_record_hash``) and leaves out
``_ingested_at``, so a re-run of unchanged data reports the same checksum.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd

from metrictrace.ingestion.contracts import QUARANTINE_REASON, apply_contract
from metrictrace.lake import LakeClient

log = logging.getLogger(__name__)

INGESTED_AT = "_ingested_at"
SOURCE_FILE = "_source_file"
RECORD_HASH = "_record_hash"
PART_FILE = "part-0.parquet"
_NULL = "\x00"
_SEPARATOR = "\x1f"


@dataclass(frozen=True)
class DropTarget:
    """Where one drop lands: raw and quarantine partitions plus its ``_meta`` record."""

    source: str  # e.g. "olist/orders", "fx", "holidays"
    contract: str  # contract (schema) name
    partition: str  # e.g. "drop_date=2017-03-01", "year=2017" or "" for reference data
    raw_prefix: str
    quarantine_prefix: str
    meta_key: str
    source_file: str


@dataclass(frozen=True)
class DropResult:
    source: str
    partition: str
    status: str  # ok | empty | failed
    input_rows: int
    row_count: int
    quarantine_count: int
    checksum: str
    reasons: dict[str, int]
    schema_error: str | None
    ingested_at: str
    run_id: str


def target_for(
    source: str, contract: str, partition: str, source_file: str, raw_root: str = "raw"
) -> DropTarget:
    suffix = f"/{partition}" if partition else ""
    meta_name = partition or "reference"
    return DropTarget(
        source=source,
        contract=contract,
        partition=partition,
        raw_prefix=f"{raw_root}/{source}{suffix}",
        quarantine_prefix=f"quarantine/{source}{suffix}",
        meta_key=f"_meta/ingestion/{source}/{meta_name}.json",
        source_file=source_file,
    )


def record_hashes(frame: pd.DataFrame) -> pd.Series:
    """SHA-256 of each row's values as delivered (strings), in column order."""
    joined = frame.astype("str").fillna(_NULL).agg(_SEPARATOR.join, axis=1)
    return joined.map(lambda row: hashlib.sha256(row.encode("utf-8")).hexdigest())


def drop_checksum(valid_hashes: pd.Series, quarantined_hashes: pd.Series) -> str:
    """Order-independent checksum of a drop's content (never of ``_ingested_at``)."""
    body = "\n".join(sorted(valid_hashes)) + "\n|\n" + "\n".join(sorted(quarantined_hashes))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def to_parquet_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, engine="pyarrow", index=False)
    return buffer.getvalue()


def land_drop(
    lake: LakeClient,
    target: DropTarget,
    frame: pd.DataFrame,
    ingested_at: datetime,
    run_id: str,
) -> DropResult:
    """Validate ``frame`` and land it, replacing anything previously landed there."""
    frame = frame.reset_index(drop=True)
    hashes = record_hashes(frame) if len(frame) else pd.Series(dtype="str")
    result = apply_contract(target.contract, frame)

    lineage: dict[str, pd.Timestamp | str] = {
        INGESTED_AT: pd.Timestamp(ingested_at),
        SOURCE_FILE: target.source_file,
    }
    lake.delete(target.raw_prefix)
    lake.delete(target.quarantine_prefix)

    valid_hashes = pd.Series(dtype="str")
    if len(result.valid):
        valid_hashes = hashes.drop(result.quarantined_index).reset_index(drop=True)
        valid = result.valid.assign(**lineage, **{RECORD_HASH: valid_hashes.to_numpy()})
        lake.write_bytes(f"{target.raw_prefix}/{PART_FILE}", to_parquet_bytes(valid))

    quarantined_hashes = hashes.loc[result.quarantined_index].reset_index(drop=True)
    if len(result.quarantined):
        quarantined = result.quarantined.assign(
            **lineage, **{RECORD_HASH: quarantined_hashes.to_numpy()}
        )
        columns = [c for c in quarantined.columns if c != QUARANTINE_REASON]
        quarantined = quarantined[[*columns, QUARANTINE_REASON]]
        lake.write_bytes(f"{target.quarantine_prefix}/{PART_FILE}", to_parquet_bytes(quarantined))

    if not result.passed:
        status = "failed"
    elif result.input_rows == 0:
        status = "empty"
    else:
        status = "ok"
    outcome = DropResult(
        source=target.source,
        partition=target.partition,
        status=status,
        input_rows=result.input_rows,
        row_count=len(result.valid),
        quarantine_count=len(result.quarantined),
        checksum=drop_checksum(valid_hashes, quarantined_hashes),
        reasons=result.reasons,
        schema_error=result.schema_error,
        ingested_at=ingested_at.isoformat(),
        run_id=run_id,
    )
    lake.write_json(target.meta_key, asdict(outcome))
    log.info("drop landed", extra=asdict(outcome))
    return outcome
