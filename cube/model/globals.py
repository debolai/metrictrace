"""Template functions for the Cube data model.

The same YAML model runs on DuckDB over the local lake and on Athena over Glue tables;
``mart()`` is the only place the two differ. Values come from the environment Cube runs
with (Docker Compose locally, the Helm chart on Kubernetes).
"""

import os
import re

from cube import TemplateContext

template = TemplateContext()

# Everything interpolated into SQL is a mart name from this model or a bucket/database
# name from configuration; refusing anything else keeps the generated SQL injection-free.
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")


def _checked(value: str) -> str:
    if not _IDENTIFIER.match(value):
        raise ValueError(f"refusing to interpolate {value!r} into SQL")
    return value


@template.function("mart")
def mart(name: str) -> str:
    """SQL selecting every row of a dbt mart."""
    table = _checked(name)
    if os.environ["APP_ENV"] == "aws":
        database = _checked(os.environ["GLUE_DATABASE"])
        return f"SELECT * FROM {database}.{table}"  # noqa: S608 - identifiers validated above
    bucket = _checked(os.environ["LAKE_BUCKET"])
    return f"SELECT * FROM read_parquet('s3://{bucket}/marts/{table}/*.parquet')"  # noqa: S608


@template.function("latest_dbt_run")
def latest_dbt_run() -> str:
    """Refresh-key SQL: changes whenever dbt rebuilds the marts."""
    return f"SELECT max(invocation_id) FROM ({mart('mart_dbt_run')}) AS run"  # noqa: S608
