from __future__ import annotations

from tests.conftest import FIXTURE_MANIFEST, FIXTURES

FIXTURE_ENV = FIXTURES.parents[1] / "tests" / "fixture.env"


def test_fixture_env_matches_fixture_manifest() -> None:
    values = dict(
        line.split("=", 1)
        for line in FIXTURE_ENV.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    assert values["SIM_START_DATE"] == FIXTURE_MANIFEST["sim_start_date"]
    assert values["SIM_END_DATE"] == FIXTURE_MANIFEST["sim_end_date"]
    assert values["API_OFFLINE"] == "true"
