from __future__ import annotations

import json

import httpx
import jwt
import pytest

from metrictrace.config import load_settings
from metrictrace.semantic.cube_client import CubeClient, CubeError, CubeQuery, TimeDimension
from tests.conftest import VALID_LOCAL, apply

QUERY = CubeQuery(
    measures=["sales.gmv"],
    dimensions=["sales.category"],
    time_dimensions=[
        TimeDimension(
            dimension="sales.purchase_date",
            granularity="month",
            date_range=["2018-02-01", "2018-02-28"],
        )
    ],
    limit=10,
)


def client(env: pytest.MonkeyPatch, handler: httpx.MockTransport) -> CubeClient:
    apply(env, VALID_LOCAL)
    return CubeClient(load_settings(env_file=None), transport=handler, sleep=lambda _: None)


def test_query_serialises_with_cube_field_names() -> None:
    assert QUERY.to_api() == {
        "measures": ["sales.gmv"],
        "dimensions": ["sales.category"],
        "timeDimensions": [
            {
                "dimension": "sales.purchase_date",
                "granularity": "month",
                "dateRange": ["2018-02-01", "2018-02-28"],
            }
        ],
        "limit": 10,
    }


def test_load_signs_requests_and_waits_for_results(env: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    answers = iter(
        [
            {"error": "Continue wait"},
            {"data": [{"sales.gmv": "12.5"}], "query": {}, "annotation": {}},
        ]
    )

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=next(answers))

    result = client(env, httpx.MockTransport(handle)).load(QUERY)
    assert result.rows == [{"sales.gmv": "12.5"}]
    assert len(calls) == 2
    assert calls[0].url.path == "/cubejs-api/v1/load"
    assert json.loads(calls[0].content)["query"]["measures"] == ["sales.gmv"]
    claims = jwt.decode(
        calls[0].headers["Authorization"],
        "cube-secret-value-0123456789abcdef",
        algorithms=["HS256"],
    )
    assert claims["exp"] > claims["iat"]


def test_errors_are_raised(env: pytest.MonkeyPatch) -> None:
    api = client(
        env, httpx.MockTransport(lambda _: httpx.Response(400, json={"error": "bad member"}))
    )
    with pytest.raises(CubeError, match="bad member"):
        api.load(QUERY)


def test_gives_up_after_deadline(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    api = CubeClient(
        load_settings(env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"error": "Continue wait"})
        ),
        timeout_seconds=0.0,
        sleep=lambda _: None,
    )
    with pytest.raises(CubeError, match="did not answer"):
        api.meta()


def test_sql_returns_the_generated_statement(env: pytest.MonkeyPatch) -> None:
    payload = {"sql": {"sql": ["SELECT sum(price) FROM t", []]}}
    api = client(env, httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    assert api.sql(QUERY) == "SELECT sum(price) FROM t"
