"""The Olist download: table definitions, loading and fetching.

The source is read as strings, exactly as delivered. Typing happens in the ingestion
contracts, so a malformed value is quarantined instead of crashing the load.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pandas as pd

from metrictrace.config import Settings

log = logging.getLogger(__name__)

KAGGLE_DATASET = "olistbr/brazilian-ecommerce"


@dataclass(frozen=True)
class TableSpec:
    """One Olist table: its lake name, source file and primary key."""

    name: str
    file_name: str
    key: tuple[str, ...]
    daily: bool  # replayed as daily drops; otherwise loaded once as reference data


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in (
        TableSpec("orders", "olist_orders_dataset.csv", ("order_id",), daily=True),
        TableSpec(
            "order_items", "olist_order_items_dataset.csv", ("order_id", "order_item_id"), True
        ),
        TableSpec(
            "order_payments",
            "olist_order_payments_dataset.csv",
            ("order_id", "payment_sequential"),
            daily=True,
        ),
        TableSpec(
            "order_reviews", "olist_order_reviews_dataset.csv", ("review_id", "order_id"), True
        ),
        TableSpec("customers", "olist_customers_dataset.csv", ("customer_id",), daily=True),
        TableSpec("sellers", "olist_sellers_dataset.csv", ("seller_id",), daily=True),
        TableSpec("products", "olist_products_dataset.csv", ("product_id",), daily=True),
        TableSpec(
            "product_category_name_translation",
            "product_category_name_translation.csv",
            ("product_category_name",),
            daily=False,
        ),
        TableSpec(
            "geolocation",
            "olist_geolocation_dataset.csv",
            ("geolocation_zip_code_prefix",),
            daily=False,
        ),
    )
}

DAILY_TABLES = tuple(name for name, spec in TABLES.items() if spec.daily)
REFERENCE_TABLES = tuple(name for name, spec in TABLES.items() if not spec.daily)


class SourceError(RuntimeError):
    """Raised when the Olist download is missing or incomplete."""


def _read_csv(handle: IO[bytes]) -> pd.DataFrame:
    # utf-8-sig strips the byte-order mark on the translation file's header.
    return pd.read_csv(
        handle, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8-sig"
    )


def load_source(path: Path) -> dict[str, pd.DataFrame]:
    """Load every Olist table from a directory of CSVs or the Kaggle zip, as strings."""
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = {Path(name).name: name for name in archive.namelist()}
            missing = [s.file_name for s in TABLES.values() if s.file_name not in members]
            if missing:
                raise SourceError(f"{path} is missing {', '.join(missing)}")
            tables = {}
            for name, spec in TABLES.items():
                with archive.open(members[spec.file_name]) as handle:
                    tables[name] = _read_csv(handle)
            return tables
    if path.is_dir():
        missing = [s.file_name for s in TABLES.values() if not (path / s.file_name).exists()]
        if missing:
            raise SourceError(f"{path} is missing {', '.join(missing)}; run `make download-data`")
        tables = {}
        for name, spec in TABLES.items():
            with (path / spec.file_name).open("rb") as handle:
                tables[name] = _read_csv(handle)
        return tables
    raise SourceError(f"Olist source not found at {path}; run `make download-data`")


def download(settings: Settings) -> Path:
    """Fetch the public dataset with kagglehub and copy its CSVs to ``olist_source_path``."""
    import kagglehub  # noqa: PLC0415 - optional at run time, only needed for this step

    if settings.kaggle_username and settings.kaggle_key:
        os.environ.setdefault("KAGGLE_USERNAME", settings.kaggle_username)
        os.environ.setdefault("KAGGLE_KEY", settings.kaggle_key.get_secret_value())
    cached = Path(kagglehub.dataset_download(KAGGLE_DATASET))
    target = settings.olist_source_path
    target.mkdir(parents=True, exist_ok=True)
    for spec in TABLES.values():
        shutil.copyfile(cached / spec.file_name, target / spec.file_name)
    log.info("olist source downloaded", extra={"path": str(target)})
    return target
