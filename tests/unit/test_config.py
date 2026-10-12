from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from metrictrace import config
from metrictrace.config import ConfigError, Settings, load_settings

VALID_LOCAL = {
    "APP_ENV": "local",
    "LAKE_BUCKET": "metrictrace-lake",
    "LAKE_ENDPOINT_URL": "http://minio:9000",
    "MINIO_ROOT_USER": "minio",
    "MINIO_ROOT_PASSWORD": "minio-secret-value",
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
    "MINIO_ROOT_USER": "",
    "MINIO_ROOT_PASSWORD": "",
    "AWS_REGION": "eu-west-1",
    "GLUE_DATABASE": "metrictrace",
    "ATHENA_WORKGROUP": "metrictrace",
    "ATHENA_OUTPUT_LOCATION": "s3://metrictrace-athena-results/",
    "GLUE_CRAWLER_NAME": "metrictrace-marts",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Start every test from an environment containing none of the settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def apply(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def problems_for(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> str:
    apply(monkeypatch, values)
    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None)
    return "\n".join(caught.value.problems)


def test_valid_local_settings_load(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    settings = load_settings(env_file=None)
    assert settings.is_local
    assert settings.lake_root == "s3://metrictrace-lake"
    assert settings.sim_start_date == date(2017, 1, 1)
    assert settings.log_level == "INFO"
    assert settings.langsmith_enabled is False


def test_valid_aws_settings_load(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, **AWS_OVERRIDES})
    settings = load_settings(env_file=None)
    assert not settings.is_local
    assert settings.lake_endpoint_url is None
    assert settings.glue_database == "metrictrace"


def test_every_missing_variable_is_named_at_once(env: pytest.MonkeyPatch) -> None:
    values = {
        k: v
        for k, v in VALID_LOCAL.items()
        if k not in {"LAKE_BUCKET", "CUBE_API_SECRET", "MINIO_ROOT_USER", "ANTHROPIC_API_KEY"}
    }
    message = problems_for(env, values)
    for name in ("LAKE_BUCKET", "CUBE_API_SECRET", "MINIO_ROOT_USER", "ANTHROPIC_API_KEY"):
        assert name in message


def test_empty_value_counts_as_missing(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, "LAKE_BUCKET": ""})
    assert "LAKE_BUCKET" in message


def test_aws_requires_catalog_settings(env: pytest.MonkeyPatch) -> None:
    values = {**VALID_LOCAL, **AWS_OVERRIDES, "GLUE_DATABASE": "", "GLUE_CRAWLER_NAME": ""}
    message = problems_for(env, values)
    assert "GLUE_DATABASE" in message
    assert "GLUE_CRAWLER_NAME" in message
    assert "MINIO_ROOT_USER" not in message


def test_fake_llm_is_rejected_on_aws(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, **AWS_OVERRIDES, "LLM_PROVIDER": "fake"})
    assert "LLM_PROVIDER=fake" in message


def test_fake_llm_needs_no_key_locally(env: pytest.MonkeyPatch) -> None:
    apply(env, {**VALID_LOCAL, "LLM_PROVIDER": "fake", "ANTHROPIC_API_KEY": ""})
    settings = load_settings(env_file=None)
    assert settings.llm_api_key is None


def test_openai_provider_requires_openai_key(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, "LLM_PROVIDER": "openai"})
    assert "OPENAI_API_KEY" in message


def test_llm_api_key_matches_provider(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    key = load_settings(env_file=None).llm_api_key
    assert key is not None
    assert key.get_secret_value() == "anthropic-secret-value"


def test_unknown_provider_is_rejected(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, "LLM_PROVIDER": "gemini"})
    assert "LLM_PROVIDER" in message


def test_replay_window_must_be_ordered(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, "SIM_START_DATE": "2018-09-01"})
    assert "SIM_START_DATE" in message


def test_endpoint_must_be_empty_on_aws(env: pytest.MonkeyPatch) -> None:
    values = {**VALID_LOCAL, **AWS_OVERRIDES, "LAKE_ENDPOINT_URL": "http://minio:9000"}
    assert "LAKE_ENDPOINT_URL" in problems_for(env, values)


def test_urls_are_validated(env: pytest.MonkeyPatch) -> None:
    message = problems_for(env, {**VALID_LOCAL, "CUBE_API_URL": "cube:4000"})
    assert "CUBE_API_URL" in message


def test_athena_output_must_be_s3(env: pytest.MonkeyPatch) -> None:
    values = {**VALID_LOCAL, **AWS_OVERRIDES, "ATHENA_OUTPUT_LOCATION": "athena-results/"}
    assert "ATHENA_OUTPUT_LOCATION" in problems_for(env, values)


def test_secrets_never_appear_in_summary_or_repr(env: pytest.MonkeyPatch) -> None:
    apply(env, VALID_LOCAL)
    settings = load_settings(env_file=None)
    rendered = repr(settings) + str(settings.safe_summary())
    for name, value in VALID_LOCAL.items():
        if "SECRET" in name or "PASSWORD" in name or "KEY" in name or "TOKEN" in name:
            assert value not in rendered
    assert settings.safe_summary()["API_BEARER_TOKEN"] == "<set>"
    assert settings.safe_summary()["OPENAI_API_KEY"] == "<not set>"


def test_env_file_is_read(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in VALID_LOCAL.items()))
    assert load_settings(env_file=env_file).lake_bucket == "metrictrace-lake"


def test_main_reports_success(env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    apply(env, VALID_LOCAL)
    assert config.main(env_file=None) == 0
    output = capsys.readouterr().out
    assert "Configuration OK (APP_ENV=local)" in output
    assert "bearer-secret-value" not in output


def test_main_reports_failure(env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert config.main(env_file=None) == 1
    assert "APP_ENV" in capsys.readouterr().err
