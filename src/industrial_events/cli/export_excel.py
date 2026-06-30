from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from industrial_events.cli.common import parse_date_arg
from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.dataset import load_event_dataset, read_event_dataset
from industrial_events.event_rows import event_rows, submission_deadline_label
from industrial_events.models import CalendarItem, EventDataset, EventRow
from industrial_events.validation import CalendarBuildError

HEADERS = (
    "Event",
    "Series",
    "Industry",
    "Topics",
    "Event Types",
    "Start",
    "End",
    "CFP Status",
    "CFP Deadline(s)",
    "Location",
    "City",
    "Country",
    "Venue",
    "URL",
    "Last Checked",
)


def main(argv: list[str] | None = None) -> int:
    today = date.today()
    parser = argparse.ArgumentParser(description="Export filtered events to an Excel .xlsx file.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--dataset", type=Path, help="Use a prebuilt dataset JSON instead of reading YAML.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--industry", action="append", default=[], help="Match domain, category, or topic slug.")
    parser.add_argument(
        "--event-name", action="append", default=[], help="Case-insensitive match in event or series name."
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=parse_date_arg,
        default=today,
        help="Keep events ending on/after this date. Defaults to today.",
    )
    parser.add_argument("--to", dest="to_date", type=parse_date_arg, help="Keep events starting on/before this date.")
    parser.add_argument(
        "--active-cfp", action="store_true", help="Keep only events with submission deadlines still open."
    )
    parser.add_argument(
        "--reference-date",
        type=parse_date_arg,
        default=today,
        help="Evaluate CFP/submission status as of this date. Defaults to today.",
    )
    args = parser.parse_args(argv)

    try:
        dataset = load_dataset(args)
        rows = [row for row in event_rows(dataset.items) if matches(row, args)]
        write_excel(args.output, (excel_row(row, args.reference_date) for row in rows))
    except (CalendarBuildError, ConfigError) as exc:
        print(f"ERROR Export failed: {exc}", file=sys.stderr)
        return 1
    return 0


def write_excel(path: Path, rows: Iterable[tuple[str, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Events"
    worksheet.append(HEADERS)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def load_dataset(args: argparse.Namespace) -> EventDataset:
    if args.dataset:
        return read_event_dataset(args.dataset)
    config = config_with_overrides(
        load_build_config(args.config),
        source_dir=args.source,
        sources_dir=args.sources,
    )
    return load_event_dataset(config)


def matches(row: EventRow, args: argparse.Namespace) -> bool:
    if args.from_date and row.end_exclusive < args.from_date:
        return False
    if args.to_date and row.start > args.to_date:
        return False
    if args.active_cfp and not any(deadline.start >= args.reference_date for deadline in row.deadlines):
        return False

    industries = {value.lower() for value in args.industry}
    if industries and not any(_matches_industry(item, industries) for item in row.conferences):
        return False

    names = [value.lower() for value in args.event_name]
    if names:
        haystack = " ".join((row.title, *(item.summary for item in row.conferences), row.conferences[0].series)).lower()
        if not all(name in haystack for name in names):
            return False
    return True


def _matches_industry(item: CalendarItem, industries: set[str]) -> bool:
    values = {item.domain, *item.categories, *item.topics}
    return bool(values & industries)


def excel_row(row: EventRow, reference_date: date) -> tuple[str, ...]:
    primary = row.conferences[0]
    open_deadlines = tuple(deadline for deadline in row.deadlines if deadline.start >= reference_date)
    deadlines = open_deadlines or row.deadlines
    return (
        row.title,
        primary.series,
        join_unique(item.domain for item in row.conferences),
        join_unique(topic for item in row.conferences for topic in item.topics),
        join_unique(event_type for item in row.conferences for event_type in item.event_types),
        row.start.isoformat(),
        row.end_exclusive.isoformat(),
        "Open" if open_deadlines else "Closed" if row.deadlines or row.start < reference_date else "TBD",
        join_unique(submission_deadline_label(deadline, row.conferences) for deadline in deadlines),
        row.location,
        join_unique(item.city for item in row.conferences),
        join_unique(item.country.upper() for item in row.conferences if item.country),
        join_unique(item.venue for item in row.conferences),
        row.url,
        row.last_checked.isoformat() if row.last_checked else "",
    )


def join_unique(values: object) -> str:
    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(str(value), None)
    return "; ".join(seen)
