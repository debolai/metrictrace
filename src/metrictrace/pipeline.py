"""Command-line entry point: ``metrictrace <command>``.

Commands are added phase by phase.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from metrictrace import transform
from metrictrace.config import ConfigError, Settings, load_settings
from metrictrace.ingestion import olist_source
from metrictrace.ingestion.runner import SOURCES, Ingestion, IngestionError
from metrictrace.lake import LakeClient
from metrictrace.logs import configure_logging

log = logging.getLogger(__name__)


def _days(args: argparse.Namespace, start: date, end: date) -> list[date]:
    if args.window:
        args.from_date, args.to_date = start, end
    elif args.date:
        return [args.date]
    if args.from_date > args.to_date:
        raise IngestionError("--from must not be after --to")
    span = (args.to_date - args.from_date).days
    return [args.from_date + timedelta(days=offset) for offset in range(span + 1)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metrictrace")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="land daily drops for simulated days")
    when = ingest.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", type=date.fromisoformat, help="one simulated day")
    when.add_argument("--from", dest="from_date", type=date.fromisoformat, help="first day")
    when.add_argument(
        "--window", action="store_true", help="every day from SIM_START_DATE to SIM_END_DATE"
    )
    ingest.add_argument("--to", dest="to_date", type=date.fromisoformat, help="last day")
    ingest.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ingest.add_argument(
        "--force-reference", action="store_true", help="reload geolocation and translations"
    )

    commands.add_parser("download-data", help="fetch the Olist dataset into OLIST_SOURCE_PATH")
    commands.add_parser("init-lake", help="create the lake bucket if missing (local only)")
    commands.add_parser(
        "transform", help="dbt source freshness and build, then publish dbt artifacts"
    )
    dbt = commands.add_parser("dbt", help="run any dbt command with the lake configured")
    dbt.add_argument("dbt_args", nargs=argparse.REMAINDER)
    return parser


EXIT_OK, EXIT_CONFIG, EXIT_INPUT, EXIT_FAILED = 0, 1, 2, 3

Handler = Callable[[argparse.Namespace, Settings, str], int]


def _download_data(args: argparse.Namespace, settings: Settings, run_id: str) -> int:
    olist_source.download(settings)
    return EXIT_OK


def _init_lake(args: argparse.Namespace, settings: Settings, run_id: str) -> int:
    LakeClient(settings).ensure_bucket(create=settings.is_local)
    return EXIT_OK


def _transform(args: argparse.Namespace, settings: Settings, run_id: str) -> int:
    result = transform.transform(settings, LakeClient(settings), run_id)
    return EXIT_OK if result.success else EXIT_FAILED


def _dbt(args: argparse.Namespace, settings: Settings, run_id: str) -> int:
    return EXIT_OK if transform.run_dbt(settings, args.dbt_args) else EXIT_FAILED


def _ingest(args: argparse.Namespace, settings: Settings, run_id: str) -> int:
    sources = list(SOURCES) if args.source == "all" else [args.source]
    try:
        ingestion = Ingestion(
            settings=settings,
            lake=LakeClient(settings),
            run_id=run_id,
            ingested_at=datetime.now(UTC),
        )
        days = _days(args, settings.sim_start_date, settings.sim_end_date)
        results = ingestion.run(days, sources, force_reference=args.force_reference)
    except (IngestionError, olist_source.SourceError) as error:
        log.error(str(error))
        return EXIT_INPUT
    statuses = Counter(result.status for result in results)
    log.info(
        "ingestion finished",
        extra={
            "drops": len(results),
            "statuses": dict(statuses),
            "rows": sum(r.row_count for r in results),
            "quarantined": sum(r.quarantine_count for r in results),
        },
    )
    return EXIT_OK


COMMANDS: dict[str, Handler] = {
    "ingest": _ingest,
    "download-data": _download_data,
    "init-lake": _init_lake,
    "transform": _transform,
    "dbt": _dbt,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest" and args.from_date and not args.to_date:
        parser.error("--from requires --to")
    try:
        settings = load_settings()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return EXIT_CONFIG
    run_id = configure_logging(settings.log_level)
    return COMMANDS[args.command](args, settings, run_id)


if __name__ == "__main__":
    raise SystemExit(main())
