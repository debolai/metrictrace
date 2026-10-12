from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from metrictrace.ingestion import http
from metrictrace.ingestion.http import CachedJsonClient, OfflineCacheMissError


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http.CachedJsonClient._fetch.retry, "sleep", lambda _: None)


def client(
    tmp_path: Path, handler: httpx.MockTransport | None = None, offline: bool = False
) -> CachedJsonClient:
    return CachedJsonClient("api", "https://example.test/v1", tmp_path, offline, transport=handler)


def test_fetch_caches_response(tmp_path: Path) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    api = client(tmp_path, httpx.MockTransport(handle))
    assert api.get_json("2018-01-01..2018-12-31", {"to": "USD", "from": "BRL"}) == {"ok": True}
    assert api.get_json("2018-01-01..2018-12-31", {"from": "BRL", "to": "USD"}) == {"ok": True}
    assert calls == ["https://example.test/v1/2018-01-01..2018-12-31?to=USD&from=BRL"]
    cached = tmp_path / "api" / "2018-01-01..2018-12-31_from=BRL_to=USD.json"
    assert json.loads(cached.read_text()) == {"ok": True}


def test_offline_reads_cache_and_never_fetches(tmp_path: Path) -> None:
    api = client(tmp_path, offline=True)
    with pytest.raises(OfflineCacheMissError):
        api.get_json("missing")
    path = api.cache_path("present")
    path.parent.mkdir(parents=True)
    path.write_text('{"cached": 1}')
    assert api.get_json("present") == {"cached": 1}


def test_retries_server_errors_then_succeeds(tmp_path: Path) -> None:
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, json=[1])])
    api = client(tmp_path, httpx.MockTransport(lambda _: next(responses)))
    assert api.get_json("x") == [1]


def test_does_not_retry_client_errors(tmp_path: Path) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    api = client(tmp_path, httpx.MockTransport(handle))
    with pytest.raises(httpx.HTTPStatusError):
        api.get_json("x")
    assert len(calls) == 1


def test_gives_up_after_max_attempts(tmp_path: Path) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("down", request=request)

    api = client(tmp_path, httpx.MockTransport(handle))
    with pytest.raises(httpx.ConnectError):
        api.get_json("x")
    assert len(calls) == http.MAX_ATTEMPTS
