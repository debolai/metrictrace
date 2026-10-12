"""Structured JSON logging with a ``run_id`` on every line."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, run_id and any extras."""

    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "run_id": self.run_id,
        }
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _RESERVED}
        )
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def new_run_id() -> str:
    """Sortable run id: UTC timestamp plus a short random suffix."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def configure_logging(level: str, run_id: str | None = None) -> str:
    """Route all logging to stdout as JSON; returns the run id in use."""
    run_id = run_id or new_run_id()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(run_id))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    return run_id
