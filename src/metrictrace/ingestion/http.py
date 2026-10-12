"""JSON over HTTP with an on-disk response cache, retries and an offline mode.

Every response is cached under ``API_CACHE_DIR`` with a readable file name. With
``API_OFFLINE=true`` only the cache is read, which is how tests and CI replay recorded
responses without touching the network.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 4


class OfflineCacheMissError(RuntimeError):
    """Raised in offline mode when a response has not been recorded."""


def _is_retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


class CachedJsonClient:
    """GET JSON from one API, caching each response under ``cache_dir/<name>/``."""

    def __init__(
        self,
        name: str,
        base_url: str,
        cache_dir: Path,
        offline: bool,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir / name
        self.offline = offline
        self._transport = transport

    def cache_path(self, path: str, params: dict[str, str] | None = None) -> Path:
        query = "_".join(f"{key}={value}" for key, value in sorted((params or {}).items()))
        stem = re.sub(r"[^A-Za-z0-9.=,_-]+", "_", f"{path.strip('/')}_{query}".strip("_"))
        return self.cache_dir / f"{stem}.json"

    def get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        cached = self.cache_path(path, params)
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        if self.offline:
            raise OfflineCacheMissError(f"no recorded response at {cached} (API_OFFLINE=true)")
        payload = self._fetch(path, params)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        return payload

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        reraise=True,
    )
    def _fetch(self, path: str, params: dict[str, str] | None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
        log.info("api response fetched", extra={"api": self.name, "url": url})
        return response.json()
