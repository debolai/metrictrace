"""Typed client for Cube's REST API (``/cubejs-api/v1``): meta, load and sql.

Requests are signed with a short-lived JWT made from ``CUBE_API_SECRET``. Cube answers a
query that is still running with ``{"error": "Continue wait"}``; the client keeps asking
until the query finishes or the deadline passes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from metrictrace.config import Settings

API_PATH = "/cubejs-api/v1"
TOKEN_TTL_SECONDS = 300
CONTINUE_WAIT = "Continue wait"


class CubeError(RuntimeError):
    """Cube rejected a request or did not answer in time."""


class TimeDimension(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    dimension: str
    granularity: str | None = None
    date_range: list[str] | None = Field(default=None, alias="dateRange")


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member: str
    operator: str
    values: list[str] = Field(default_factory=list)


class CubeQuery(BaseModel):
    """A Cube JSON query. Field names follow Cube's REST API."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_dimensions: list[TimeDimension] = Field(default_factory=list, alias="timeDimensions")
    filters: list[Filter] = Field(default_factory=list)
    order: dict[str, str] = Field(default_factory=dict)
    limit: int | None = None

    def to_api(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)


class CubeResult(BaseModel):
    rows: list[dict[str, Any]]
    annotation: dict[str, Any]
    query: dict[str, Any]


class CubeClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._secret = settings.cube_api_secret.get_secret_value()
        self._base = settings.cube_api_url.rstrip("/") + API_PATH
        self._transport = transport
        self._timeout = timeout_seconds
        self._poll = poll_interval_seconds
        self._sleep = sleep

    def _token(self) -> str:
        now = int(time.time())
        return jwt.encode({"iat": now, "exp": now + TOKEN_TTL_SECONDS}, self._secret, "HS256")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        deadline = time.monotonic() + self._timeout
        with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
            while True:
                response = client.request(
                    method,
                    f"{self._base}{path}",
                    json=body,
                    headers={"Authorization": self._token()},
                )
                payload = response.json()
                if isinstance(payload, dict) and payload.get("error") == CONTINUE_WAIT:
                    if time.monotonic() >= deadline:
                        raise CubeError(f"Cube did not answer {path} within {self._timeout}s")
                    self._sleep(self._poll)
                    continue
                if response.status_code != httpx.codes.OK or "error" in payload:
                    raise CubeError(f"Cube {path} failed ({response.status_code}): {payload}")
                return payload

    def meta(self) -> dict[str, Any]:
        """Public views and their members, with ``meta`` (``/v1/meta``)."""
        result: dict[str, Any] = self._request("GET", "/meta")
        return result

    def load(self, query: CubeQuery) -> CubeResult:
        """Run a query (``/v1/load``)."""
        payload = self._request("POST", "/load", {"query": query.to_api()})
        return CubeResult(
            rows=payload["data"], annotation=payload.get("annotation", {}), query=payload["query"]
        )

    def sql(self, query: CubeQuery) -> str:
        """The SQL Cube generates for a query, for the audit log (``/v1/sql``)."""
        payload = self._request("POST", "/sql", {"query": query.to_api()})
        sql = payload["sql"]["sql"]
        statement: str = sql[0] if isinstance(sql, list) else sql
        return statement
