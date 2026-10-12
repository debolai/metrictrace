from __future__ import annotations

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
    "CUBE_API_SECRET": "cube-secret-value",
    "API_BEARER_TOKEN": "bearer-secret-value",
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
