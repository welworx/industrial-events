from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from industrial_events import site as build_site

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update one event YAML file in place.")
    parser.add_argument("event_file", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--url")
    parser.add_argument("--status", choices=tuple(sorted(build_site.STATUS_MAP)))
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
    parser.add_argument("--deadline-status", choices=tuple(sorted(build_site.STATUS_MAP)))
    parser.add_argument("--deadline-source-url")
    parser.add_argument("--history-note", default="previous value before automated update")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    event_path = args.event_file if args.event_file.is_absolute() else ROOT / args.event_file
    event = build_site.load_event_file(event_path)
    before = yaml.safe_dump(event, sort_keys=False, allow_unicode=False)

    apply_event_updates(event, args)
    apply_source_update(event, args.source_url, args.source_type, args.source_scope, args.last_checked)
    apply_deadline_update(event, args)
    validate_event(event_path, event)

    after = yaml.safe_dump(event, sort_keys=False, allow_unicode=False)
    changed = before != after
    if changed and not args.dry_run:
        event_path.write_text(after, encoding="utf-8", newline="\n")

    print(
        json.dumps(
            {
                "event_file": event_path.relative_to(ROOT).as_posix(),
                "changed": changed,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 0


def apply_event_updates(event: dict[str, Any], args: argparse.Namespace) -> None:
    for key in ("name", "url", "status", "timezone", "city", "country", "venue", "address"):
        value = getattr(args, key)
        if value is not None:
            event[key] = value
    for key in ("start", "end"):
        value = getattr(args, key)
        if value is not None:
            event[key] = value.isoformat()
    for key in ("latitude", "longitude"):
        value = getattr(args, key)
        if value is not None:
            event[key] = value

    replacement_types = expand_filter(args.event_type)
    if replacement_types:
        event["event_types"] = sorted(replacement_types)

    added_types = expand_filter(args.add_event_type)
    if added_types:
        current = set(build_site.require_slug_list(Path("<event>"), event, "event_types", "event"))
        event["event_types"] = sorted(current | added_types)


def apply_source_update(
    event: dict[str, Any],
    source_url: str | None,
    source_type: str,
    source_scope: str,
    last_checked: date | None,
) -> None:
    if not source_url and last_checked is None:
        return
    if not source_url:
        raise build_site.CalendarBuildError("--last-checked requires --source-url")

    sources = event.setdefault("sources", [])
    if not isinstance(sources, list):
        raise build_site.CalendarBuildError("event sources must be a list")
    source = next((item for item in sources if isinstance(item, dict) and item.get("url") == source_url), None)
    if source is None:
        source = {"type": source_type, "scope": source_scope, "url": source_url}
        sources.append(source)
    if last_checked is not None:
        source["last_checked"] = last_checked.isoformat()


def apply_deadline_update(event: dict[str, Any], args: argparse.Namespace) -> None:
    if not any((args.deadline_type, args.deadline_name, args.deadline_date, args.deadline_url, args.deadline_status)):
        return
    if not args.deadline_type:
        raise build_site.CalendarBuildError("deadline updates require --deadline-type")

    deadlines = event.setdefault("deadlines", [])
    if not isinstance(deadlines, list):
        raise build_site.CalendarBuildError("event deadlines must be a list")
    deadline = next(
        (item for item in deadlines if isinstance(item, dict) and item.get("type") == args.deadline_type), None
    )
    if deadline is None:
        if args.deadline_date is None:
            raise build_site.CalendarBuildError("new deadlines require --deadline-date")
        deadline = {"type": args.deadline_type, "date": args.deadline_date.isoformat()}
        deadlines.append(deadline)

    if args.deadline_name is not None:
        deadline["name"] = args.deadline_name
    if args.deadline_url is not None:
        deadline["url"] = args.deadline_url
    if args.deadline_status is not None:
        deadline["status"] = args.deadline_status
    if args.deadline_date is not None:
        update_deadline_date(deadline, args.deadline_date, args.deadline_url, args.history_note)
    if args.deadline_source_url:
        apply_deadline_source_update(deadline, args.deadline_source_url, args.last_checked)


def update_deadline_date(deadline: dict[str, Any], new_date: date, url: str | None, note: str) -> None:
    old_date = deadline.get("date")
    new_value = new_date.isoformat()
    if old_date and old_date != new_value:
        history = deadline.setdefault("history", [])
        if not isinstance(history, list):
            raise build_site.CalendarBuildError("deadline history must be a list")
        if not any(isinstance(item, dict) and item.get("date") == old_date for item in history):
            entry = {"date": old_date, "note": note}
            if url:
                entry["url"] = url
            history.append(entry)
    deadline["date"] = new_value


def apply_deadline_source_update(deadline: dict[str, Any], source_url: str, last_checked: date | None) -> None:
    sources = deadline.setdefault("sources", [])
    if not isinstance(sources, list):
        raise build_site.CalendarBuildError("deadline sources must be a list")
    source = next((item for item in sources if isinstance(item, dict) and item.get("url") == source_url), None)
    if source is None:
        source = {"type": "cfp", "scope": "deadline", "url": source_url}
        sources.append(source)
    if last_checked is not None:
        source["last_checked"] = last_checked.isoformat()


def validate_event(event_path: Path, event: dict[str, Any]) -> None:
    build_site.validate_unknown_keys(event_path, event, build_site.EVENT_KEYS)
    build_site.require_slug_list(event_path, event, "event_types", "event")
    start = build_site.optional_date(event_path, event, "start", "event")
    end = build_site.optional_date(event_path, event, "end", "event")
    if (start is None) != (end is None):
        raise build_site.CalendarBuildError("start and end must be provided together")
    if start and end and end < start:
        raise build_site.CalendarBuildError("end must be on or after start")
    if ("latitude" in event) != ("longitude" in event):
        raise build_site.CalendarBuildError("latitude and longitude must be provided together")


def expand_filter(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        expanded.update(part.strip() for part in value.split(",") if part.strip())
    for value in expanded:
        if not build_site.SLUG_RE.fullmatch(value):
            raise build_site.CalendarBuildError(f"{value!r} must be a lowercase slug")
    return expanded


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from exc


if __name__ == "__main__":
    raise SystemExit(main())
