from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from industrial_events.cli.common import parse_date_arg
from industrial_events.update_event import EventUpdate, update_event_file
from industrial_events.validation import STATUS_MAP, CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update one event YAML file in place.")
    parser.add_argument("event_file", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--url")
    parser.add_argument("--status", choices=tuple(sorted(STATUS_MAP)))
    parser.add_argument("--start", type=parse_date_arg)
    parser.add_argument("--end", type=parse_date_arg)
    parser.add_argument("--timezone")
    parser.add_argument("--city")
    parser.add_argument("--country")
    parser.add_argument("--venue")
    parser.add_argument("--address")
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument(
        "--event-type", action="append", default=[], help="Replace event_types; repeat or comma-separate."
    )
    parser.add_argument(
        "--add-event-type", action="append", default=[], help="Append event_types; repeat or comma-separate."
    )
    parser.add_argument("--source-url", help="Event source URL to add or update.")
    parser.add_argument("--source-type", default="event-site")
    parser.add_argument("--source-scope", default="event")
    parser.add_argument("--last-checked", type=parse_date_arg)
    parser.add_argument("--deadline-type")
    parser.add_argument("--deadline-name")
    parser.add_argument("--deadline-date", type=parse_date_arg)
    parser.add_argument("--deadline-url")
    parser.add_argument("--deadline-status", choices=tuple(sorted(STATUS_MAP)))
    parser.add_argument("--deadline-source-url")
    parser.add_argument("--history-note", default="previous value before automated update")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        payload = update_event_file(
            args.event_file,
            EventUpdate(
                name=args.name,
                url=args.url,
                status=args.status,
                start=args.start,
                end=args.end,
                timezone=args.timezone,
                city=args.city,
                country=args.country,
                venue=args.venue,
                address=args.address,
                latitude=args.latitude,
                longitude=args.longitude,
                event_type=args.event_type,
                add_event_type=args.add_event_type,
                source_url=args.source_url,
                source_type=args.source_type,
                source_scope=args.source_scope,
                last_checked=args.last_checked,
                deadline_type=args.deadline_type,
                deadline_name=args.deadline_name,
                deadline_date=args.deadline_date,
                deadline_url=args.deadline_url,
                deadline_status=args.deadline_status,
                deadline_source_url=args.deadline_source_url,
                history_note=args.history_note,
            ),
            dry_run=args.dry_run,
        )
        print(json.dumps(payload, indent=2))
        return 0
    except (CalendarBuildError, OSError) as exc:
        print(f"ERROR Update failed: {exc}", file=sys.stderr)
        return 1
