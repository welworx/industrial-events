from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from zoneinfo import available_timezones

from industrial_events.config import load_yaml_mapping
from industrial_events.models import SeriesMetadata
from industrial_events.validation import (
    SLUG_RE,
    CalendarBuildError,
    optional_str,
    optional_url,
    require_date,
    require_str,
    source_urls,
    validate_unknown_keys,
)

PROGRAM_SUFFIX = ".program.yaml"
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
TIMEZONE_RE = re.compile(r"^(?:UTC|[A-Za-z]+(?:/[A-Za-z0-9_+-]+)+)$")


def is_valid_timezone(name: str) -> bool:
    """Exact IANA check when the tz database is installed, shape check otherwise."""
    zones = available_timezones()
    return name in zones if zones else bool(TIMEZONE_RE.fullmatch(name))


PROGRAM_KEYS = {"series", "edition", "timezone", "sources", "sessions"}
SESSION_KEYS = {
    "id",
    "title",
    "type",
    "date",
    "start",
    "end",
    "room",
    "room_detail",
    "track",
    "note",
    "chairs",
    "chairs_truncated",
    "talks",
}
SESSION_TYPES = {
    "oral",
    "plenary",
    "keynote",
    "panel",
    "poster",
    "workshop",
    "tutorial",
    "short-course",
    "ceremony",
    "social",
}
CHAIR_KEYS = {"name", "affiliation"}
TALK_KEYS = {"paper_id", "title", "start", "end", "authors", "abstract", "abstract_url", "doi", "keywords"}
AUTHOR_KEYS = {"name", "presenting", "affiliations"}


def program_file_for(event_path: Path) -> Path:
    """Program file that belongs next to an event file: lead-zinc-2026.yaml -> lead-zinc-2026.program.yaml."""
    return event_path.with_name(f"{event_path.stem}{PROGRAM_SUFFIX}")


def program_files(series_dir: Path) -> list[Path]:
    return sorted(series_dir.glob(f"*{PROGRAM_SUFFIX}"))


def load_program_file(path: Path, metadata: SeriesMetadata, event_start: date, event_end: date) -> dict:
    """Load and validate a program file against its series and event dates."""
    data = load_yaml_mapping(path, error_type=CalendarBuildError, read_label="program file")
    validate_program_record(path, data, metadata, event_start, event_end)
    return data


def validate_program_record(
    path: Path, data: dict, metadata: SeriesMetadata, event_start: date, event_end: date
) -> None:
    edition = path.name.removesuffix(PROGRAM_SUFFIX)
    validate_unknown_keys(path, data, PROGRAM_KEYS)
    if require_str(path, data, "series") != metadata.slug:
        raise CalendarBuildError(f"{path}: field 'series' must be {metadata.slug!r}")
    if require_str(path, data, "edition") != edition:
        raise CalendarBuildError(f"{path}: field 'edition' must match the file name ({edition!r})")
    if not is_valid_timezone(require_str(path, data, "timezone")):
        raise CalendarBuildError(f"{path}: program field 'timezone' must be an IANA name such as Europe/Berlin")
    if not source_urls(path, data, "program"):
        raise CalendarBuildError(f"{path}: program field 'sources' must list the original program source(s)")
    for index, source in enumerate(data["sources"], start=1):
        label = f"program source {index}"
        require_str(path, source, "type", label)
        require_date(path, source, "last_checked", label)
    if not any(source["type"] == "program" for source in data["sources"]):
        raise CalendarBuildError(f"{path}: program field 'sources' needs at least one source with type 'program'")

    sessions = data.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise CalendarBuildError(f"{path}: program field 'sessions' must be a non-empty list")
    session_ids: set[str] = set()
    paper_ids: set[str] = set()
    scheduled: dict[date, list[tuple[str, str, str, str]]] = {}
    for index, session in enumerate(sessions, start=1):
        validate_session(path, session, f"session {index}", event_start, event_end, session_ids, paper_ids)
        check_sessions_do_not_clash(path, session, f"session {index}", scheduled)


def check_sessions_do_not_clash(path: Path, session: dict, label: str, scheduled: dict) -> None:
    """A room hosts one session at a time; a session without a room occupies every room."""
    room = " ".join(str(session.get("room") or "").casefold().split())
    day = require_date(path, session, "date", label)
    for start, end, other_id, other_room in scheduled.get(day, []):
        same_room = not room or not other_room or room == other_room
        if same_room and session["start"] < end and start < session["end"]:
            raise CalendarBuildError(f"{path}: {label} ({session['id']}) overlaps {other_id} on {day}")
    scheduled.setdefault(day, []).append((session["start"], session["end"], session["id"], room))


def validate_session(
    path: Path,
    session: object,
    label: str,
    event_start: date,
    event_end: date,
    session_ids: set[str],
    paper_ids: set[str],
) -> None:
    if not isinstance(session, dict):
        raise CalendarBuildError(f"{path}: {label} must be a YAML object")
    validate_unknown_keys(path, session, SESSION_KEYS, label)
    session_id = require_str(path, session, "id", label)
    if not SLUG_RE.fullmatch(session_id):
        raise CalendarBuildError(f"{path}: {label} field 'id' must be a lowercase slug")
    if session_id in session_ids:
        raise CalendarBuildError(f"{path}: {label} duplicate session id {session_id!r}")
    session_ids.add(session_id)
    label = f"{label} ({session_id})"
    require_str(path, session, "title", label)
    session_type = require_str(path, session, "type", label)
    if session_type not in SESSION_TYPES:
        raise CalendarBuildError(f"{path}: {label} field 'type' must be one of {sorted(SESSION_TYPES)}")
    session_date = require_date(path, session, "date", label)
    if not event_start <= session_date <= event_end:
        raise CalendarBuildError(
            f"{path}: {label} date {session_date} is outside the event ({event_start}..{event_end})"
        )
    start = require_time(path, session, "start", label)
    end = require_time(path, session, "end", label)
    if end <= start:
        raise CalendarBuildError(f"{path}: {label} end must be after start")
    for key in ("room", "room_detail", "track", "note"):
        optional_str(session, key, path, label)
    if not isinstance(session.get("chairs_truncated") or False, bool):
        raise CalendarBuildError(f"{path}: {label} field 'chairs_truncated' must be true or false")
    validate_chairs(path, optional_list(path, session, "chairs", label), label)

    talks = optional_list(path, session, "talks", label)
    previous_end = ""
    for index, talk in enumerate(talks, start=1):
        previous_end = validate_talk(path, talk, f"{label} talk {index}", (start, end), previous_end, paper_ids)


def optional_list(path: Path, data: dict, key: str, label: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a list")
    return value


def validate_chairs(path: Path, chairs: list, label: str) -> None:
    for index, chair in enumerate(chairs, start=1):
        chair_label = f"{label} chair {index}"
        if not isinstance(chair, dict):
            raise CalendarBuildError(f"{path}: {chair_label} must be a YAML object")
        validate_unknown_keys(path, chair, CHAIR_KEYS, chair_label)
        require_str(path, chair, "name", chair_label)
        optional_str(chair, "affiliation", path, chair_label)


def validate_talk(
    path: Path,
    talk: object,
    label: str,
    window: tuple[str, str],
    previous_end: str,
    paper_ids: set[str],
) -> str:
    if not isinstance(talk, dict):
        raise CalendarBuildError(f"{path}: {label} must be a YAML object")
    validate_unknown_keys(path, talk, TALK_KEYS, label)
    require_str(path, talk, "title", label)
    if "paper_id" in talk:
        paper_id = talk["paper_id"]
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise CalendarBuildError(f"{path}: {label} field 'paper_id' must be a non-empty string (quote numeric IDs)")
        if paper_id != paper_id.strip():
            raise CalendarBuildError(f"{path}: {label} field 'paper_id' must not have leading or trailing spaces")
        if paper_id in paper_ids:
            raise CalendarBuildError(f"{path}: {label} duplicate paper_id {paper_id!r}")
        paper_ids.add(paper_id)
        label = f"{label} ({paper_id})"
    if ("start" in talk) != ("end" in talk):
        raise CalendarBuildError(f"{path}: {label} start and end must be provided together")
    if "start" in talk:
        start = require_time(path, talk, "start", label)
        end = require_time(path, talk, "end", label)
        if end <= start:
            raise CalendarBuildError(f"{path}: {label} end must be after start")
        if start < window[0] or end > window[1]:
            raise CalendarBuildError(f"{path}: {label} {start}-{end} is outside its session ({window[0]}-{window[1]})")
        if start < previous_end:
            raise CalendarBuildError(
                f"{path}: {label} starts at {start} before the previous talk ends ({previous_end})"
            )
        previous_end = end
    for key in ("abstract", "doi"):
        optional_str(talk, key, path, label)
    optional_url(path, talk, "abstract_url", label)
    keywords = optional_list(path, talk, "keywords", label)
    if not all(isinstance(k, str) and k.strip() for k in keywords):
        raise CalendarBuildError(f"{path}: {label} field 'keywords' must be a list of strings")
    validate_authors(path, talk.get("authors"), label)
    return previous_end


def validate_authors(path: Path, authors: object, label: str) -> None:
    if not isinstance(authors, list) or not authors:
        raise CalendarBuildError(f"{path}: {label} field 'authors' must be a non-empty list")
    for index, author in enumerate(authors, start=1):
        author_label = f"{label} author {index}"
        if not isinstance(author, dict):
            raise CalendarBuildError(f"{path}: {author_label} must be a YAML object")
        validate_unknown_keys(path, author, AUTHOR_KEYS, author_label)
        require_str(path, author, "name", author_label)
        if not isinstance(author.get("presenting", False), bool):
            raise CalendarBuildError(f"{path}: {author_label} field 'presenting' must be true or false")
        affiliations = optional_list(path, author, "affiliations", author_label)
        if not all(isinstance(a, str) and a.strip() for a in affiliations):
            raise CalendarBuildError(f"{path}: {author_label} field 'affiliations' must be a list of strings")


def require_time(path: Path, data: dict, key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a quoted HH:MM string")
    return value
