from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path

import pytest

from metrictrace.config import Settings

VALID_LOCAL = {
    "APP_ENV": "local",
    "LAKE_BUCKET": "metrictrace-lake",
    "LAKE_ENDPOINT_URL": "http://localhost:9000",
    "RUSTFS_ACCESS_KEY": "rustfs-user",
    "RUSTFS_SECRET_KEY": "rustfs-secret-value",
    "AWS_REGION": "us-east-1",
    "CUBE_API_URL": "http://cube:4000",
    "API_URL": "http://api:8000",
    "LLM_PROVIDER": "anthropic",
    "LLM_MODEL": "test-model",
    "ANTHROPIC_API_KEY": "anthropic-secret-value",
    "CUBE_API_SECRET": "cube-secret-value-0123456789abcdef",
    "API_BEARER_TOKEN": "bearer-secret-value-0123456789abcdef",
    "SIM_START_DATE": "2017-01-01",
    "SIM_END_DATE": "2018-08-31",
}

AWS_OVERRIDES = {
    "APP_ENV": "aws",
    "LAKE_ENDPOINT_URL": "",
    "RUSTFS_ACCESS_KEY": "",
    "RUSTFS_SECRET_KEY": "",
    "AWS_REGION": "eu-west-1",
    "GLUE_DATABASE": "metrictrace",
    "ATHENA_WORKGROUP": "metrictrace",
    "ATHENA_OUTPUT_LOCATION": "s3://metrictrace-athena-results/",
    "GLUE_CRAWLER_NAME": "metrictrace-marts",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Start from an environment containing none of the settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def apply(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


FIXTURES = Path(__file__).resolve().parents[1] / "data" / "fixtures"
FIXTURE_MANIFEST = json.loads((FIXTURES / "fixture.json").read_text())


def fixture_settings_env() -> dict[str, str]:
    """Settings that replay the committed fixture with recorded API responses, offline."""
    return {
        **VALID_LOCAL,
        "OLIST_SOURCE_PATH": str(FIXTURES / "olist"),
        "API_CACHE_DIR": str(FIXTURES / "api"),
        "API_OFFLINE": "true",
        "SIM_START_DATE": FIXTURE_MANIFEST["sim_start_date"],
        "SIM_END_DATE": FIXTURE_MANIFEST["sim_end_date"],
    }


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests may reach local services (RustFS) but never the internet."""
    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = str(address[0]) if isinstance(address, tuple) else str(address)
            if host not in _LOOPBACK and not ipaddress.ip_address(host).is_loopback:
                raise RuntimeError(f"tests must not use the network (tried {address})")
        real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
