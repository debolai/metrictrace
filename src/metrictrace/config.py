"""Application settings.

Every path, bucket, endpoint, model name and secret used by MetricTrace comes from here.
``APP_ENV`` (``local`` or ``aws``) selects which conditional settings are required.

Validation reports every problem at once, naming each variable, so a misconfigured
environment can be fixed in one pass. ``make check-env`` runs the same validation
without starting anything.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "aws"]
LlmProvider = Literal["anthropic", "openai", "fake"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

DEFAULT_ENV_FILE = Path(".env")

_REQUIRED_WHEN_LOCAL = ("lake_endpoint_url", "minio_root_user", "minio_root_password")
_REQUIRED_WHEN_AWS = (
    "glue_database",
    "athena_workgroup",
    "athena_output_location",
    "glue_crawler_name",
)
_KEY_FOR_PROVIDER = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}


class ConfigError(Exception):
    """Raised when the environment does not describe a valid configuration."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))


class Settings(BaseSettings):
    """Validated settings, read from environment variables and an optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv
    log_level: LogLevel = "INFO"

    # Lake
    lake_bucket: str
    lake_endpoint_url: str | None = None
    minio_root_user: str | None = None
    minio_root_password: SecretStr | None = None
    aws_region: str
    aws_profile: str | None = None

    # AWS catalog and query engine
    glue_database: str | None = None
    athena_workgroup: str | None = None
    athena_output_location: str | None = None
    glue_crawler_name: str | None = None

    # Services
    cube_api_url: str
    api_url: str

    # Source data
    kaggle_username: str | None = None
    kaggle_key: SecretStr | None = None

    # LLM
    llm_provider: LlmProvider
    llm_model: str
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    # Service secrets
    cube_api_secret: SecretStr
    api_bearer_token: SecretStr

    # Tracing
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str | None = None

    # Replay window
    sim_start_date: date
    sim_end_date: date

    @model_validator(mode="before")
    @classmethod
    def _check_required(cls, data: Any) -> Any:
        """Report every missing variable, unconditional and conditional, in one error."""
        if not isinstance(data, dict):
            return data

        def present(name: str) -> bool:
            return data.get(name) not in (None, "")

        missing = [
            name
            for name, field in cls.model_fields.items()
            if field.is_required() and not present(name)
        ]
        app_env = data.get("app_env")
        if app_env == "local":
            missing += [name for name in _REQUIRED_WHEN_LOCAL if not present(name)]
        elif app_env == "aws":
            missing += [name for name in _REQUIRED_WHEN_AWS if not present(name)]
        key_name = _KEY_FOR_PROVIDER.get(str(data.get("llm_provider")))
        if key_name is not None and not present(key_name):
            missing.append(key_name)

        if missing:
            raise ValueError("missing: " + ", ".join(name.upper() for name in missing))
        return data

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        problems: list[str] = []
        if self.app_env == "aws" and self.llm_provider == "fake":
            problems.append("LLM_PROVIDER=fake is only allowed for tests, not APP_ENV=aws")
        if self.app_env == "aws" and self.lake_endpoint_url:
            problems.append("LAKE_ENDPOINT_URL must be empty when APP_ENV=aws (real S3 is used)")
        if self.sim_start_date > self.sim_end_date:
            problems.append("SIM_START_DATE must not be after SIM_END_DATE")
        for name in ("cube_api_url", "api_url", "lake_endpoint_url"):
            value = getattr(self, name)
            if value and not value.startswith(("http://", "https://")):
                problems.append(f"{name.upper()} must start with http:// or https://")
        if self.athena_output_location and not self.athena_output_location.startswith("s3://"):
            problems.append("ATHENA_OUTPUT_LOCATION must start with s3://")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def lake_root(self) -> str:
        """URI of the lake bucket; zones are prefixes beneath it."""
        return f"s3://{self.lake_bucket}"

    @property
    def llm_api_key(self) -> SecretStr | None:
        """API key for the configured provider; ``None`` for the fake provider."""
        key_name = _KEY_FOR_PROVIDER.get(self.llm_provider)
        if key_name is None:
            return None
        key: SecretStr | None = getattr(self, key_name)
        return key

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_api_key is not None

    def safe_summary(self) -> dict[str, str]:
        """Settings for display: secrets are reported only as set or not set."""
        summary: dict[str, str] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                shown = "<set>"
            elif value is None:
                shown = "<not set>"
            else:
                shown = str(value)
            summary[name.upper()] = shown
        return summary


def _describe(error: ValidationError) -> list[str]:
    problems: list[str] = []
    for item in error.errors():
        cause = item.get("ctx", {}).get("error")
        message = str(cause) if cause is not None else item["msg"]
        location = ".".join(str(part).upper() for part in item["loc"])
        problems.append(f"{location}: {message}" if location else message)
    return problems


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Load and validate settings, raising :class:`ConfigError` that names every problem."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None


def main(env_file: Path | None = DEFAULT_ENV_FILE) -> int:
    """Entry point for ``make check-env``."""
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Configuration OK (APP_ENV={settings.app_env})")
    for name, value in settings.safe_summary().items():
        print(f"  {name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
