"""Command-line entry point: ``metrictrace <command>``.

Commands are added phase by phase; Phase 2 provides ``ingest`` and ``download-data``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from metrictrace.config import ConfigError, load_settings
from metrictrace.ingestion import olist_source
from metrictrace.ingestion.runner import SOURCES, Ingestion, IngestionError
from metrictrace.lake import LakeClient
from metrictrace.logs import configure_logging

log = logging.getLogger(__name__)


def _days(args: argparse.Namespace) -> list[date]:
    if args.date:
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
    ingest.add_argument("--to", dest="to_date", type=date.fromisoformat, help="last day")
    ingest.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    ingest.add_argument(
        "--force-reference", action="store_true", help="reload geolocation and translations"
    )

    commands.add_parser("download-data", help="fetch the Olist dataset into OLIST_SOURCE_PATH")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ingest" and args.from_date and not args.to_date:
        parser.error("--from requires --to")
    try:
        settings = load_settings()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    run_id = configure_logging(settings.log_level)

    if args.command == "download-data":
        olist_source.download(settings)
        return 0

    sources = list(SOURCES) if args.source == "all" else [args.source]
    try:
        ingestion = Ingestion(
            settings=settings,
            lake=LakeClient(settings),
            run_id=run_id,
            ingested_at=datetime.now(UTC),
        )
        results = ingestion.run(_days(args), sources, force_reference=args.force_reference)
    except (IngestionError, olist_source.SourceError) as error:
        log.error(str(error))
        return 2
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
