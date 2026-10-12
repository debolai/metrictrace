"""Lake integration tests: need the local stack (`make up`) and a filled-in .env."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from metrictrace.config import Settings, load_settings
from metrictrace.lake import LakeClient, connect

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def client(settings: Settings) -> LakeClient:
    return LakeClient(settings)


@pytest.fixture
def prefix(client: LakeClient) -> Iterator[str]:
    """A unique scratch prefix, removed after the test."""
    scratch = f"_test/{uuid.uuid4().hex}"
    yield scratch
    client.delete(scratch)


def test_bucket_exists(client: LakeClient) -> None:
    assert client.bucket_exists(), "run `make up` to create the lake bucket"


def test_object_round_trip(client: LakeClient, prefix: str) -> None:
    client.write_bytes(f"{prefix}/a.bin", b"metrictrace")
    client.write_json(f"{prefix}/b.json", {"rows": 3})
    assert client.read_bytes(f"{prefix}/a.bin") == b"metrictrace"
    assert client.read_json(f"{prefix}/b.json") == {"rows": 3}
    assert client.list(prefix) == [f"{prefix}/a.bin", f"{prefix}/b.json"]


def test_overwrite_is_idempotent(client: LakeClient, prefix: str) -> None:
    for _ in range(2):
        client.write_json(f"{prefix}/meta.json", {"checksum": "abc"})
    assert client.list(prefix) == [f"{prefix}/meta.json"]
    assert client.read_json(f"{prefix}/meta.json") == {"checksum": "abc"}


def test_delete_prefix(client: LakeClient, prefix: str) -> None:
    client.write_bytes(f"{prefix}/x/1", b"1")
    client.write_bytes(f"{prefix}/x/2", b"2")
    client.write_bytes(f"{prefix}/y/1", b"1")
    client.delete(f"{prefix}/x")
    assert client.list(prefix) == [f"{prefix}/y/1"]


def test_duckdb_writes_and_reads_hive_partitioned_parquet(
    settings: Settings, client: LakeClient, prefix: str
) -> None:
    target = client.uri(f"{prefix}/orders")
    con = connect(settings)
    try:
        con.execute(
            f"""
            copy (
                select * from (values
                    ('o1', date '2017-01-01'),
                    ('o2', date '2017-01-01'),
                    ('o3', date '2017-01-02')
                ) as t(order_id, drop_date)
            ) to '{target}' (format parquet, partition_by (drop_date))
            """
        )
        rows = con.sql(
            f"""
            select drop_date::varchar as drop_date, count(*) as n
            from read_parquet('{target}/*/*.parquet', hive_partitioning = true)
            group by 1 order by 1
            """
        ).fetchall()
    finally:
        con.close()

    assert rows == [("2017-01-01", 2), ("2017-01-02", 1)]
    written = client.list(f"{prefix}/orders")
    assert {key.split("/")[-2] for key in written} == {
        "drop_date=2017-01-01",
        "drop_date=2017-01-02",
    }
