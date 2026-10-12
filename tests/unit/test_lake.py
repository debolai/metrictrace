from __future__ import annotations

import duckdb
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from metrictrace import lake
from metrictrace.config import load_settings
from metrictrace.lake import LakeClient, LakeKeyError, LakeSetupError, validate_key
from tests.conftest import AWS_OVERRIDES, VALID_LOCAL, apply


@pytest.fixture
def client(env: pytest.MonkeyPatch) -> LakeClient:
    apply(env, VALID_LOCAL)
    fs = MemoryFileSystem(skip_instance_cache=True)
    fs.store.clear()  # the memory store is shared by every instance
    fs.pseudo_dirs[:] = [""]
    fs.mkdir("metrictrace-lake")
    return LakeClient(load_settings(env_file=None), fs=fs)


@pytest.mark.parametrize(
    "key", ["", "/raw/x", "s3://bucket/raw", "raw//x", "raw/../secrets", "./raw"]
)
def test_invalid_keys_are_rejected(key: str) -> None:
    with pytest.raises(LakeKeyError):
        validate_key(key)


def test_trailing_slash_is_normalised() -> None:
    assert validate_key("raw/olist/") == "raw/olist"


def test_uri(client: LakeClient) -> None:
    assert client.uri("marts/fct_orders") == "s3://metrictrace-lake/marts/fct_orders"


def test_bytes_and_json_round_trip(client: LakeClient) -> None:
    client.write_bytes("raw/a.bin", b"\x00\x01")
    assert client.read_bytes("raw/a.bin") == b"\x00\x01"
    client.write_json("_meta/x.json", {"b": 2, "a": 1})
    assert client.read_json("_meta/x.json") == {"a": 1, "b": 2}
    assert client.exists("_meta/x.json")
    assert client.bucket_exists()


def test_json_bytes_are_deterministic(client: LakeClient) -> None:
    client.write_json("_meta/one.json", {"b": 2, "a": 1})
    client.write_json("_meta/two.json", {"a": 1, "b": 2})
    assert client.read_bytes("_meta/one.json") == client.read_bytes("_meta/two.json")


def test_list_and_delete(client: LakeClient) -> None:
    for key in ("raw/t/drop_date=2017-01-02/p.parquet", "raw/t/drop_date=2017-01-01/p.parquet"):
        client.write_bytes(key, b"x")
    client.write_bytes("raw/other/p.parquet", b"x")
    assert client.list("raw/t") == [
        "raw/t/drop_date=2017-01-01/p.parquet",
        "raw/t/drop_date=2017-01-02/p.parquet",
    ]
    client.delete("raw/t/drop_date=2017-01-01")
    assert client.list("raw/t") == ["raw/t/drop_date=2017-01-02/p.parquet"]
    assert client.list("raw/missing") == []
    client.delete("raw/missing")  # deleting nothing is not an error


def test_local_secret_sql(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, "RUSTFS_SECRET_KEY": "it's-secret"})
    sql = lake.s3_secret_sql(load_settings(env_file=None))
    assert "ENDPOINT 'localhost:9000'" in sql
    assert "USE_SSL false" in sql
    assert "URL_STYLE 'path'" in sql
    assert "SCOPE 's3://metrictrace-lake'" in sql
    assert "SECRET 'it''s-secret'" in sql  # quotes are escaped


def test_https_endpoint_uses_ssl(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, "LAKE_ENDPOINT_URL": "https://lake.example:9443"})
    sql = lake.s3_secret_sql(load_settings(env_file=None))
    assert "USE_SSL true" in sql
    assert "ENDPOINT 'lake.example:9443'" in sql


def test_aws_secret_uses_credential_chain(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, **AWS_OVERRIDES})
    sql = lake.s3_secret_sql(load_settings(env_file=None))
    assert "PROVIDER credential_chain" in sql
    assert "REGION 'eu-west-1'" in sql
    assert "KEY_ID" not in sql


def test_aws_filesystem_has_no_static_keys(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, **AWS_OVERRIDES})
    fs = lake.build_filesystem(load_settings(env_file=None))
    assert fs.key is None
    assert fs.secret is None


def test_local_filesystem_uses_endpoint_and_path_style(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    fs = lake.build_filesystem(load_settings(env_file=None))
    assert fs.endpoint_url == "http://localhost:9000"
    assert fs.config_kwargs == {"s3": {"addressing_style": "path"}}


def test_connect_loads_extensions_and_scopes_secret(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    con = lake.connect(load_settings(env_file=None))
    try:
        loaded = dict(
            con.sql(
                "select extension_name, loaded from duckdb_extensions() "
                "where extension_name in ('httpfs', 'aws')"
            ).fetchall()
        )
        assert loaded == {"httpfs": True, "aws": True}
        secrets = con.sql("select name, scope from duckdb_secrets()").fetchall()
        assert secrets == [("lake", ["s3://metrictrace-lake"])]
        autoinstall = con.sql("select current_setting('autoinstall_known_extensions')")
        assert autoinstall.fetchone() == (False,)
    finally:
        con.close()


def test_connect_fails_clearly_without_extension(
    env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply(env, VALID_LOCAL)
    monkeypatch.setattr(lake, "REQUIRED_EXTENSIONS", ("not_a_real_extension",))
    with pytest.raises(LakeSetupError, match="make duckdb-extensions"):
        lake.connect(load_settings(env_file=None))


def test_duckdb_never_autoinstalls(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    con = lake.connect(load_settings(env_file=None))
    try:
        with pytest.raises(duckdb.Error):
            con.sql("select * from read_xlsx('missing.xlsx')")  # excel extension not loaded
    finally:
        con.close()


def test_secret_only_applies_to_lake_bucket(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    con = lake.connect(load_settings(env_file=None))
    try:
        inside = con.sql("from which_secret('s3://metrictrace-lake/raw/x.parquet', 's3')")
        outside = con.sql("from which_secret('s3://some-other-bucket/x.parquet', 's3')")
        assert [row[0] for row in inside.fetchall()] == ["lake"]
        assert outside.fetchall() == []
    finally:
        con.close()
