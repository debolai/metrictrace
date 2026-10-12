from __future__ import annotations

import json
from pathlib import Path

import pytest
from fsspec.implementations.memory import MemoryFileSystem

from metrictrace import transform
from metrictrace.config import load_settings
from metrictrace.lake import LakeClient
from tests.conftest import AWS_OVERRIDES, VALID_LOCAL, apply


def test_local_env_points_dbt_at_the_lake(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    values = transform.dbt_env(load_settings(env_file=None))
    assert values["DBT_TARGET"] == "local"
    assert values["LAKE_BUCKET"] == "metrictrace-lake"
    assert values["DBT_S3_ENDPOINT"] == "localhost:9000"
    assert values["DBT_S3_USE_SSL"] == "false"
    assert values["DBT_DUCKDB_PATH"].endswith("dbt/target/metrictrace.duckdb")


def test_aws_env_has_no_static_keys(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, **AWS_OVERRIDES})
    values = transform.dbt_env(load_settings(env_file=None))
    assert values["DBT_TARGET"] == "aws"
    assert not any(key.startswith("DBT_S3_") for key in values)


def test_subprocess_env_contains_nothing_else(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    env.setenv("API_BEARER_TOKEN", "must-not-leak")
    env.setenv("AWS_PROFILE", "")  # empty AWS values break botocore and are dropped
    env.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", "/var/run/token")  # IRSA passes through
    values = transform.subprocess_env(load_settings(env_file=None))
    assert "must-not-leak" not in values.values()
    assert "AWS_PROFILE" not in values
    assert values["AWS_WEB_IDENTITY_TOKEN_FILE"] == "/var/run/token"
    assert values["DBT_SEND_ANONYMOUS_USAGE_STATS"] == "false"


def test_publish_artifacts(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    apply(env, {**VALID_LOCAL, "DBT_PROJECT_DIR": str(tmp_path)})
    settings = load_settings(env_file=None)
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text("{}")
    (target / "run_results.json").write_text(json.dumps({"metadata": {"invocation_id": "inv-1"}}))
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    fs.mkdir(settings.lake_bucket)
    lake = LakeClient(settings, fs=fs)

    published = transform.publish_artifacts(settings, lake, "run-7", success=True)

    assert published == [
        "_meta/dbt/run_id=run-7/manifest.json",
        "_meta/dbt/run_id=run-7/run_results.json",
    ]
    latest = lake.read_json("_meta/dbt/latest.json")
    assert (latest["run_id"], latest["dbt_invocation_id"], latest["success"]) == (
        "run-7",
        "inv-1",
        True,
    )
