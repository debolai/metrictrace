from __future__ import annotations

import shutil
import sys
import types
import zipfile
from pathlib import Path

import pytest

from metrictrace.config import load_settings
from metrictrace.ingestion import olist_source
from metrictrace.ingestion.olist_source import TABLES, SourceError, load_source
from tests.conftest import FIXTURES, apply, fixture_settings_env

SOURCE = FIXTURES / "olist"


def test_directory_and_zip_load_identically(tmp_path: Path) -> None:
    archive = tmp_path / "brazilian-ecommerce.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for spec in TABLES.values():
            zipped.write(SOURCE / spec.file_name, arcname=spec.file_name)
    from_dir = load_source(SOURCE)
    from_zip = load_source(archive)
    assert set(from_dir) == set(TABLES)
    for name in TABLES:
        assert from_dir[name].equals(from_zip[name]), name


def test_values_are_strings_and_empty_cells_are_missing() -> None:
    orders = load_source(SOURCE)["orders"]
    assert str(orders["order_id"].dtype) == "str"
    assert orders["order_delivered_customer_date"].isna().any()
    translation = load_source(SOURCE)["product_category_name_translation"]
    assert translation.columns[0] == "product_category_name"  # byte-order mark stripped


def test_missing_file_is_named(tmp_path: Path) -> None:
    shutil.copytree(SOURCE, tmp_path / "olist")
    (tmp_path / "olist" / "olist_orders_dataset.csv").unlink()
    with pytest.raises(SourceError, match=r"olist_orders_dataset\.csv"):
        load_source(tmp_path / "olist")


def test_zip_missing_file_is_named(tmp_path: Path) -> None:
    archive = tmp_path / "partial.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.write(SOURCE / "olist_orders_dataset.csv", arcname="olist_orders_dataset.csv")
    with pytest.raises(SourceError, match=r"olist_customers_dataset\.csv"):
        load_source(archive)


def test_absent_source_points_to_download(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="make download-data"):
        load_source(tmp_path / "nothing-here")


def test_download_copies_every_table(
    env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    apply(env, {**fixture_settings_env(), "OLIST_SOURCE_PATH": str(tmp_path / "raw")})
    fake = types.ModuleType("kagglehub")
    fake.dataset_download = lambda handle: str(SOURCE)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kagglehub", fake)
    target = olist_source.download(load_settings(env_file=None))
    assert sorted(p.name for p in target.iterdir()) == sorted(s.file_name for s in TABLES.values())
