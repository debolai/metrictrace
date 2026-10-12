from __future__ import annotations

from pathlib import Path

import pytest

from metrictrace import pipeline
from tests.conftest import apply, fixture_settings_env


def test_parser_requires_a_day_or_range() -> None:
    with pytest.raises(SystemExit):
        pipeline.build_parser().parse_args(["ingest"])


def test_from_requires_to(env: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        pipeline.main(["ingest", "--from", "2018-03-01"])


def test_day_outside_window_exits_with_error(env: pytest.MonkeyPatch) -> None:
    apply(env, fixture_settings_env())
    assert pipeline.main(["ingest", "--date", "2016-01-01", "--source", "fx"]) == 2


def test_reversed_range_exits_with_error(env: pytest.MonkeyPatch) -> None:
    apply(env, fixture_settings_env())
    assert pipeline.main(["ingest", "--from", "2018-03-02", "--to", "2018-03-01"]) == 2


def test_invalid_configuration_exits_with_error(
    env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    assert pipeline.main(["ingest", "--date", "2018-03-01"]) == 1
