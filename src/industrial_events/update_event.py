from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from industrial_events.config import ROOT, repo_relative_path
from industrial_events.data import load_event_file
from industrial_events.validation import (
    CalendarBuildError,
    expand_slug_filter,
    require_slug_list,
    validate_event_record,
)


@dataclass(frozen=True)
class EventUpdate:
    name: str | None = None
    url: str | None = None
    status: str | None = None
    start: date | None = None
    end: date | None = None
    timezone: str | None = None
    city: str | None = None
    country: str | None = None
    venue: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    event_type: list[str] = field(default_factory=list)
    add_event_type: list[str] = field(default_factory=list)
    source_url: str | None = None
    source_type: str = "event-site"
    source_scope: str = "event"
    last_checked: date | None = None
    deadline_type: str | None = None
    deadline_name: str | None = None
    deadline_date: date | None = None
    deadline_url: str | None = None
    deadline_status: str | None = None
    deadline_source_url: str | None = None
    history_note: str = "previous value before automated update"


def update_event_file(
    event_file: Path,
    update: EventUpdate,
    *,
    dry_run: bool = False,
    root: Path = ROOT,
) -> dict[str, Any]:
    if update.last_checked and not (update.source_url or update.deadline_source_url):
        raise CalendarBuildError("--last-checked requires --source-url or --deadline-source-url")
    event_path = event_file if event_file.is_absolute() else root / event_file
    event = load_event_file(event_path)
    before = yaml.safe_dump(event, sort_keys=False, allow_unicode=False)

    apply_event_updates(event, update)
    apply_source_update(
        event,
        update.source_url,
        update.source_type,
        update.source_scope,
        update.last_checked if update.source_url else None,
    )
    apply_deadline_update(event, update)
    validate_event_record(event_path, event)

    after = yaml.safe_dump(event, sort_keys=False, allow_unicode=False)
    changed = before != after
    if changed and not dry_run:
        event_path.write_text(after, encoding="utf-8", newline="\n")

    return {
        "event_file": repo_relative_path(event_path),
        "changed": changed,
        "dry_run": dry_run,
    }


def apply_event_updates(event: dict[str, Any], update: EventUpdate) -> None:
    for key in ("name", "url", "status", "timezone", "city", "country", "venue", "address"):
        value = getattr(update, key)
        if value is not None:
            event[key] = value
    for key in ("start", "end"):
        value = getattr(update, key)
        if value is not None:
            event[key] = value.isoformat()
    for key in ("latitude", "longitude"):
        value = getattr(update, key)
        if value is not None:
            event[key] = value

    replacement_types = expand_slug_filter(update.event_type)
    if replacement_types:
        event["event_types"] = sorted(replacement_types)

    added_types = expand_slug_filter(update.add_event_type)
    if added_types:
        current = set(require_slug_list(Path("<event>"), event, "event_types", "event"))
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
        raise CalendarBuildError("--last-checked requires --source-url")

    sources = event.setdefault("sources", [])
    if not isinstance(sources, list):
        raise CalendarBuildError("event sources must be a list")
    source = next((item for item in sources if isinstance(item, dict) and item.get("url") == source_url), None)
    if source is None:
        source = {"type": source_type, "scope": source_scope, "url": source_url}
        sources.append(source)
    if last_checked is not None:
        source["last_checked"] = last_checked.isoformat()


def apply_deadline_update(event: dict[str, Any], update: EventUpdate) -> None:
    if not any(
        (
            update.deadline_type,
            update.deadline_name,
            update.deadline_date,
            update.deadline_url,
            update.deadline_status,
            update.deadline_source_url,
        )
    ):
        return
    if not update.deadline_type:
        raise CalendarBuildError("deadline updates require --deadline-type")

    deadlines = event.setdefault("deadlines", [])
    if not isinstance(deadlines, list):
        raise CalendarBuildError("event deadlines must be a list")
    deadline = next(
        (item for item in deadlines if isinstance(item, dict) and item.get("type") == update.deadline_type), None
    )
    if deadline is None:
        if update.deadline_date is None:
            raise CalendarBuildError("new deadlines require --deadline-date")
        deadline = {"type": update.deadline_type, "date": update.deadline_date.isoformat()}
        deadlines.append(deadline)

    old_deadline_url = deadline.get("url")
    if update.deadline_name is not None:
        deadline["name"] = update.deadline_name
    if update.deadline_url is not None:
        deadline["url"] = update.deadline_url
    if update.deadline_status is not None:
        deadline["status"] = update.deadline_status
    if update.deadline_date is not None:
        update_deadline_date(deadline, update.deadline_date, old_deadline_url, update.history_note)
    if update.deadline_source_url:
        apply_deadline_source_update(deadline, update.deadline_source_url, update.last_checked)


def update_deadline_date(deadline: dict[str, Any], new_date: date, url: str | None, note: str) -> None:
    old_date = deadline.get("date")
    new_value = new_date.isoformat()
    if old_date and old_date != new_value:
        history = deadline.setdefault("history", [])
        if not isinstance(history, list):
            raise CalendarBuildError("deadline history must be a list")
        if not any(isinstance(item, dict) and item.get("date") == old_date for item in history):
            entry = {"date": old_date, "note": note}
            if url:
                entry["url"] = url
            history.append(entry)
    deadline["date"] = new_value


def apply_deadline_source_update(deadline: dict[str, Any], source_url: str, last_checked: date | None) -> None:
    sources = deadline.setdefault("sources", [])
    if not isinstance(sources, list):
        raise CalendarBuildError("deadline sources must be a list")
    source = next((item for item in sources if isinstance(item, dict) and item.get("url") == source_url), None)
    if source is None:
        source = {"type": "cfp", "scope": "deadline", "url": source_url}
        sources.append(source)
    if last_checked is not None:
        source["last_checked"] = last_checked.isoformat()
