"""Lake access: object storage through fsspec/s3fs, and DuckDB connections that read it.

The lake is one bucket; zones (``raw/``, ``marts/``, ``_meta/`` ...) are key prefixes.
Locally the bucket lives in RustFS (S3-compatible); on AWS it is S3, reached through
the default credential chain (IRSA in pods). Callers use bucket-relative keys and never
build endpoints or credentials themselves.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import duckdb
import s3fs
from fsspec import AbstractFileSystem

from metrictrace.config import Settings

# Installed ahead of time (``make duckdb-extensions`` or the image build) and only
# loaded at run time, so no code path downloads extensions.
REQUIRED_EXTENSIONS: tuple[str, ...] = ("httpfs", "aws")

_DUCKDB_CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_persistent_secrets": False,
}
# Set after httpfs loads. DuckDB otherwise opens a new connection per HTTP request, which
# exhausts a local port forwarder within seconds and costs a TLS handshake per request on S3.
_HTTP_SETTINGS = ("set httpfs_connection_caching = true",)


class LakeKeyError(ValueError):
    """Raised for a key that is not a clean bucket-relative path."""


class LakeSetupError(RuntimeError):
    """Raised when the lake or DuckDB cannot be set up as configured."""


def validate_key(key: str) -> str:
    """Return ``key`` if it is a clean bucket-relative path, otherwise raise."""
    if not key:
        raise LakeKeyError("lake key must not be empty")
    if "://" in key or key.startswith("/"):
        raise LakeKeyError(f"lake key must be relative to the bucket: {key!r}")
    parts = key.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise LakeKeyError(f"lake key has an empty or relative segment: {key!r}")
    return key.rstrip("/")


def build_filesystem(settings: Settings) -> AbstractFileSystem:
    """S3 filesystem for the configured environment, with listing caches off."""
    common: dict[str, Any] = {
        "client_kwargs": {"region_name": settings.aws_region},
        "use_listings_cache": False,
        "skip_instance_cache": True,
    }
    if not settings.is_local:
        return s3fs.S3FileSystem(**common)
    if settings.rustfs_access_key is None or settings.rustfs_secret_key is None:
        raise LakeSetupError("local lake credentials are not configured")
    return s3fs.S3FileSystem(
        key=settings.rustfs_access_key,
        secret=settings.rustfs_secret_key.get_secret_value(),
        endpoint_url=settings.lake_endpoint_url,
        config_kwargs={"s3": {"addressing_style": "path"}},
        **common,
    )


class LakeClient:
    """Reads and writes objects in the lake bucket by bucket-relative key."""

    def __init__(self, settings: Settings, fs: AbstractFileSystem | None = None) -> None:
        self.bucket = settings.lake_bucket
        self.fs = fs if fs is not None else build_filesystem(settings)

    def uri(self, key: str) -> str:
        """Full ``s3://`` URI for a key, as DuckDB and dbt expect."""
        return f"s3://{self.bucket}/{validate_key(key)}"

    def _path(self, key: str) -> str:
        return f"{self.bucket}/{validate_key(key)}"

    def bucket_exists(self) -> bool:
        return bool(self.fs.exists(self.bucket))

    def ensure_bucket(self, create: bool) -> None:
        """Create the bucket if it is missing and ``create`` is allowed; otherwise raise.

        Locally the bucket is ours to create; on AWS Terraform owns it.
        """
        if self.bucket_exists():
            return
        if not create:
            raise LakeSetupError(f"lake bucket {self.bucket!r} does not exist")
        self.fs.mkdir(self.bucket)

    def exists(self, key: str) -> bool:
        return bool(self.fs.exists(self._path(key)))

    def write_bytes(self, key: str, data: bytes) -> None:
        self.fs.pipe_file(self._path(key), data)

    def read_bytes(self, key: str) -> bytes:
        data: bytes = self.fs.cat_file(self._path(key))
        return data

    def write_json(self, key: str, payload: Any) -> None:
        """Write JSON with sorted keys, so identical payloads give identical bytes."""
        text = json.dumps(payload, sort_keys=True, indent=2, default=str)
        self.write_bytes(key, text.encode("utf-8") + b"\n")

    def read_json(self, key: str) -> Any:
        return json.loads(self.read_bytes(key))

    def list(self, prefix: str) -> list[str]:
        """Bucket-relative keys of every object under ``prefix``, sorted."""
        root = self._path(prefix)
        if not self.fs.exists(root):
            return []
        found: list[str] = self.fs.find(root)
        bucket_prefix = f"{self.bucket}/"
        # Filesystems differ on a leading slash ("/bucket/key" vs "bucket/key").
        return sorted(path.lstrip("/").removeprefix(bucket_prefix) for path in found)

    def delete(self, prefix: str) -> None:
        """Delete every object under ``prefix`` (or the single object ``prefix``)."""
        keys = self.list(prefix)
        if keys:
            self.fs.rm([f"{self.bucket}/{key}" for key in keys])


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def s3_secret_sql(settings: Settings) -> str:
    """``CREATE SECRET`` statement giving DuckDB access to the lake bucket only."""
    scope = _sql_literal(f"s3://{settings.lake_bucket}")
    region = _sql_literal(settings.aws_region)
    if not settings.is_local:
        return (
            "CREATE SECRET lake (TYPE s3, PROVIDER credential_chain, "
            f"REGION {region}, SCOPE {scope})"
        )
    if (
        settings.lake_endpoint_url is None
        or settings.rustfs_access_key is None
        or settings.rustfs_secret_key is None
    ):
        raise LakeSetupError("local lake endpoint or credentials are not configured")
    endpoint = urlsplit(settings.lake_endpoint_url)
    use_ssl = "true" if endpoint.scheme == "https" else "false"
    return (
        "CREATE SECRET lake (TYPE s3, "
        f"KEY_ID {_sql_literal(settings.rustfs_access_key)}, "
        f"SECRET {_sql_literal(settings.rustfs_secret_key.get_secret_value())}, "
        f"REGION {region}, ENDPOINT {_sql_literal(endpoint.netloc)}, "
        f"URL_STYLE 'path', USE_SSL {use_ssl}, SCOPE {scope})"
    )


def install_extensions() -> None:
    """Download the DuckDB extensions MetricTrace needs (setup step, needs network)."""
    con = duckdb.connect()
    try:
        for name in REQUIRED_EXTENSIONS:
            con.install_extension(name)
    finally:
        con.close()


def connect(settings: Settings, database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """DuckDB connection that can read and write the lake bucket.

    Extensions are loaded, never installed, and secrets are not persisted to disk.
    """
    con = duckdb.connect(database, config=_DUCKDB_CONFIG)
    try:
        for name in REQUIRED_EXTENSIONS:
            try:
                con.load_extension(name)
            except duckdb.Error as error:
                raise LakeSetupError(
                    f"DuckDB extension {name!r} is not installed; run `make duckdb-extensions`"
                ) from error
        for statement in _HTTP_SETTINGS:
            con.execute(statement)
        con.execute(s3_secret_sql(settings))
    except BaseException:
        con.close()
        raise
    return con
