"""Runs ingestion for a range of simulated days and sources."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from metrictrace.config import Settings
from metrictrace.ingestion import fx_rates, holidays
from metrictrace.ingestion.http import CachedJsonClient
from metrictrace.ingestion.landing import DropResult, land_drop, target_for
from metrictrace.ingestion.olist_drops import ReplayPlan
from metrictrace.ingestion.olist_source import DAILY_TABLES, REFERENCE_TABLES, TABLES, load_source
from metrictrace.lake import LakeClient

log = logging.getLogger(__name__)

SOURCES = ("olist", "fx", "holidays")


class IngestionError(ValueError):
    """Raised for an ingestion request outside what the configuration allows."""


@dataclass
class Ingestion:
    """Everything one ingestion run needs; clients are injectable for tests."""

    settings: Settings
    lake: LakeClient
    run_id: str
    ingested_at: datetime
    plan_factory: Callable[[], ReplayPlan] | None = None
    fx_client: CachedJsonClient | None = None
    holidays_client: CachedJsonClient | None = None

    def __post_init__(self) -> None:
        s = self.settings
        self._fx_api = self.fx_client or CachedJsonClient(
            "frankfurter", s.fx_api_base_url, s.api_cache_dir, s.api_offline
        )
        self._holidays_api = self.holidays_client or CachedJsonClient(
            "nager_date", s.holidays_api_base_url, s.api_cache_dir, s.api_offline
        )
        self._plan: ReplayPlan | None = None
        self._fx_years: dict[int, dict[str, Any]] = {}

    @property
    def plan(self) -> ReplayPlan:
        if self._plan is None:
            if self.plan_factory is not None:
                self._plan = self.plan_factory()
            else:
                tables = load_source(self.settings.olist_source_path)
                self._plan = ReplayPlan(
                    tables, self.settings.sim_start_date, self.settings.sim_end_date
                )
        return self._plan

    def run(
        self, days: Iterable[date], sources: Iterable[str], force_reference: bool = False
    ) -> list[DropResult]:
        days = sorted(set(days))
        sources = set(sources)
        unknown = sources - set(SOURCES)
        if unknown:
            raise IngestionError(f"unknown sources: {', '.join(sorted(unknown))}")
        start, end = self.settings.sim_start_date, self.settings.sim_end_date
        outside = [day for day in days if not start <= day <= end]
        if outside:
            raise IngestionError(
                f"{outside[0]} is outside the replay window {start}..{end} "
                "(SIM_START_DATE..SIM_END_DATE)"
            )

        results: list[DropResult] = []
        if "olist" in sources:
            results += self._olist_reference(force_reference)
        if "holidays" in sources:
            for year in sorted({day.year for day in days}):
                results.append(self._holidays(year))
        for day in days:
            if "olist" in sources:
                results += [self._olist_drop(table, day) for table in DAILY_TABLES]
            if "fx" in sources:
                results.append(self._fx(day))
        return results

    def _land(
        self, source: str, contract: str, partition: str, source_file: str, frame: Any
    ) -> DropResult:
        target = target_for(source, contract, partition, source_file)
        return land_drop(self.lake, target, frame, self.ingested_at, self.run_id)

    def _olist_drop(self, table: str, day: date) -> DropResult:
        return self._land(
            f"olist/{table}",
            table,
            f"drop_date={day.isoformat()}",
            TABLES[table].file_name,
            self.plan.drop(table, day),
        )

    def _olist_reference(self, force: bool) -> list[DropResult]:
        results = []
        for table in REFERENCE_TABLES:
            target = target_for(f"olist/{table}", table, "", TABLES[table].file_name)
            if not force and self.lake.exists(target.meta_key):
                continue
            results.append(
                land_drop(
                    self.lake, target, self.plan.reference(table), self.ingested_at, self.run_id
                )
            )
        return results

    def _fx(self, day: date) -> DropResult:
        if day.year not in self._fx_years:
            self._fx_years[day.year] = fx_rates.fetch_year(self._fx_api, day.year)
        return self._land(
            "fx",
            "fx",
            f"drop_date={day.isoformat()}",
            f"frankfurter:{day.year}",
            fx_rates.fx_drop(self._fx_years[day.year], day),
        )

    def _holidays(self, year: int) -> DropResult:
        return self._land(
            "holidays",
            "holidays",
            f"year={year}",
            f"nager.date:{year}",
            holidays.holidays_for_year(self._holidays_api, year),
        )
