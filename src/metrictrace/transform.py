"""Run dbt against the lake and publish its artifacts for the agent.

dbt reads its profile from environment variables. This module derives them from the
validated settings, so the dbt project never sees a hardcoded endpoint or credential.

dbt runs as a subprocess with an explicit, minimal environment, from a working directory
outside the repository: dbt-core 1.12 loads any ``.env`` it finds from its working
directory upwards into the process environment, which would leak every setting
(secrets included) into dbt and break botocore with empty values such as
``AWS_PROFILE=``. Importing dbt in this process would trigger the same loading.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from metrictrace.config import Settings
from metrictrace.lake import LakeClient

log = logging.getLogger(__name__)

ARTIFACTS = ("manifest.json", "run_results.json", "sources.json")


def duckdb_path(settings: Settings) -> Path:
    """Scratch DuckDB file for one transform; removed when the next transform starts."""
    return (settings.dbt_project_dir / "target" / "metrictrace.duckdb").resolve()


def dbt_env(settings: Settings) -> dict[str, str]:
    """Environment variables read by dbt/profiles.yml and dbt_project.yml."""
    env = {
        "DBT_TARGET": settings.app_env,
        "DBT_DUCKDB_PATH": str(duckdb_path(settings)),
        "LAKE_BUCKET": settings.lake_bucket,
        "AWS_REGION": settings.aws_region,
        "SIM_START_DATE": settings.sim_start_date.isoformat(),
        "SIM_END_DATE": settings.sim_end_date.isoformat(),
    }
    if settings.is_local:
        if (
            settings.lake_endpoint_url is None
            or settings.rustfs_access_key is None
            or settings.rustfs_secret_key is None
        ):
            raise ValueError("local lake endpoint or credentials are not configured")
        endpoint = urlsplit(settings.lake_endpoint_url)
        env |= {
            "DBT_S3_KEY_ID": settings.rustfs_access_key,
            "DBT_S3_SECRET": settings.rustfs_secret_key.get_secret_value(),
            "DBT_S3_ENDPOINT": endpoint.netloc,
            "DBT_S3_USE_SSL": "true" if endpoint.scheme == "https" else "false",
        }
    return env


# Passed through from this process: enough to run Python, plus any AWS variables that
# carry real values (on EKS, IRSA supplies AWS_ROLE_ARN and AWS_WEB_IDENTITY_TOKEN_FILE).
_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE")


def subprocess_env(settings: Settings) -> dict[str, str]:
    """The complete environment dbt runs with: nothing else leaks in."""
    env = {key: os.environ[key] for key in _PASSTHROUGH if os.environ.get(key)}
    env |= {key: value for key, value in os.environ.items() if key.startswith("AWS_") and value}
    env |= dbt_env(settings)
    env["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    return env


def run_dbt(settings: Settings, args: Sequence[str]) -> bool:
    """Run one dbt command for ``settings``; returns whether it succeeded."""
    project = str(settings.dbt_project_dir.resolve())
    command = [
        sys.executable,
        "-c",
        "from dbt.cli.main import cli; cli()",
        *args,
        "--project-dir",
        project,
        "--profiles-dir",
        project,
    ]
    with tempfile.TemporaryDirectory(prefix="metrictrace-dbt-") as workdir:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
            command, cwd=workdir, env=subprocess_env(settings), check=False
        )
    return completed.returncode == 0


@dataclass(frozen=True)
class TransformResult:
    run_id: str
    success: bool
    freshness_success: bool
    published: list[str]


def publish_artifacts(
    settings: Settings, lake: LakeClient, run_id: str, success: bool
) -> list[str]:
    """Copy dbt artifacts to ``_meta/dbt/run_id=<id>/`` and point ``latest.json`` at them."""
    target = settings.dbt_project_dir / "target"
    prefix = f"_meta/dbt/run_id={run_id}"
    published = []
    for name in ARTIFACTS:
        path = target / name
        if path.exists():
            lake.write_bytes(f"{prefix}/{name}", path.read_bytes())
            published.append(f"{prefix}/{name}")
    invocation = None
    results = target / "run_results.json"
    if results.exists():
        invocation = json.loads(results.read_text())["metadata"].get("invocation_id")
    lake.write_json(
        "_meta/dbt/latest.json",
        {
            "run_id": run_id,
            "dbt_invocation_id": invocation,
            "success": success,
            "finished_at": datetime.now(UTC).isoformat(),
            "artifacts": published,
        },
    )
    return published


def transform(settings: Settings, lake: LakeClient, run_id: str) -> TransformResult:
    """Source freshness, then ``dbt build``, then publish artifacts (even on failure)."""
    scratch = duckdb_path(settings)
    for leftover in (scratch, scratch.with_suffix(".duckdb.wal")):
        leftover.unlink(missing_ok=True)
    freshness_success = run_dbt(settings, ["source", "freshness"])
    # sources.json is overwritten by the next command's artifacts only if it writes one,
    # so it is kept aside to publish with the build's manifest and run results.
    target = settings.dbt_project_dir / "target"
    sources = (target / "sources.json").read_bytes() if (target / "sources.json").exists() else None
    build_success = run_dbt(settings, ["build"])
    if sources is not None:
        (target / "sources.json").write_bytes(sources)
    published = publish_artifacts(settings, lake, run_id, build_success)
    log.info(
        "transform finished",
        extra={"success": build_success, "freshness_success": freshness_success},
    )
    return TransformResult(run_id, build_success, freshness_success, published)
