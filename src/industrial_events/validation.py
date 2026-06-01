from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from industrial_events.models import CoLocation
from industrial_events.url_utils import is_safe_external_url

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SERIES_METADATA_KEYS = {
    "series",
    "slug",
    "website",
    "website_status",
    "description",
    "recurrence",
    "categories",
    "topics",
    "sources",
}
EVENT_KEYS = {
    "name",
    "event_types",
    "start",
    "end",
    "timezone",
    "city",
    "country",
    "venue",
    "address",
    "latitude",
    "longitude",
    "url",
    "url_status",
    "status",
    "co_located_with",
    "sources",
    "deadlines",
}
DEADLINE_KEYS = {"type", "name", "date", "url", "status", "sources", "history"}
DEADLINE_HISTORY_KEYS = {"date", "announced", "url", "note"}
CO_LOCATED_KEYS = {"group", "name", "url", "series"}
SOURCE_KEYS = {"type", "url", "scope", "note", "last_checked", "last_updated"}
SOURCE_PAGE_KEYS = {"name", "slug", "url", "type", "categories", "topics", "last_checked", "last_updated", "note"}
STATUS_MAP = {
    "confirmed": "CONFIRMED",
    "tentative": "TENTATIVE",
    "estimated": "TENTATIVE",
    "cancelled": "CANCELLED",
}
URL_STATUS_VALUES = {"active", "inactive", "restricted", "intermittent"}
RECURRENCE_VALUES = {"recurring", "one-off", "unknown"}
SUBMISSION_DEADLINE_KEYWORDS = ("abstract", "manuscript", "paper", "papers", "poster", "proposal", "submission")


class CalendarBuildError(Exception):
    pass


def expand_slug_filter(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        expanded.update(part.strip() for part in value.split(",") if part.strip())
    for value in expanded:
        if not SLUG_RE.fullmatch(value):
            raise CalendarBuildError(f"{value!r} must be a lowercase slug")
    return expanded


def validate_unknown_keys(path: Path, data: dict, allowed: set[str], label: str = "top level") -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise CalendarBuildError(f"{path}: unknown {label} field(s): {', '.join(unknown)}")


def require_str(path: Path, data: dict, key: str, label: str = "top level") -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CalendarBuildError(f"{path}: {label} field {key!r} is required")
    return value.strip()


def optional_str(data: dict, key: str, path: Path | None = None, label: str = "top level") -> str:
    if key not in data or data[key] is None:
        return ""
    value = data[key]
    if isinstance(value, str):
        return value.strip()
    field = f"{label} field {key!r}"
    if path is not None:
        raise CalendarBuildError(f"{path}: {field} must be a string")
    raise CalendarBuildError(f"{field} must be a string")


def require_url(path: Path, data: dict, key: str, label: str = "top level") -> str:
    value = require_str(path, data, key, label)
    if not is_safe_external_url(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be an http(s) URL")
    return value


def optional_url(path: Path, data: dict, key: str, label: str = "top level") -> str:
    value = optional_str(data, key, path, label)
    if not value:
        return ""
    if not is_safe_external_url(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be an http(s) URL")
    return value


def require_slug(path: Path, data: dict, key: str, label: str = "top level") -> str:
    value = require_str(path, data, key, label)
    if not SLUG_RE.fullmatch(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a lowercase slug")
    return value


def require_slug_list(path: Path, data: dict, key: str, label: str = "top level") -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a non-empty list")
    slugs = tuple(item for item in value if isinstance(item, str) and SLUG_RE.fullmatch(item))
    if len(slugs) != len(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must contain only lowercase slugs")
    return slugs


def optional_slug_list(path: Path, data: dict, key: str, label: str = "top level") -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a list")
    slugs = tuple(item for item in value if isinstance(item, str) and SLUG_RE.fullmatch(item))
    if len(slugs) != len(value):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must contain only lowercase slugs")
    return slugs


def source_urls(path: Path, data: dict, label: str) -> tuple[str, ...]:
    value = data.get("sources", [])
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: {label} field 'sources' must be a list of YAML objects")

    urls: list[str] = []
    for index, source in enumerate(value, start=1):
        source_label = f"{label} source {index}"
        if not isinstance(source, dict):
            raise CalendarBuildError(f"{path}: {source_label} must be a YAML object")
        validate_unknown_keys(path, source, SOURCE_KEYS, source_label)
        urls.append(require_url(path, source, "url", source_label))
        validate_source_dates(path, source, source_label)
        for key in ("type", "scope"):
            source_type = optional_str(source, key)
            if source_type and not SLUG_RE.fullmatch(source_type):
                raise CalendarBuildError(f"{path}: {source_label} field {key!r} must be a lowercase slug")
    return tuple(urls)


def source_checked_dates(path: Path, data: dict, label: str) -> tuple[date, ...]:
    value = data.get("sources", [])
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: {label} field 'sources' must be a list of YAML objects")

    checked_dates: list[date] = []
    for index, source in enumerate(value, start=1):
        source_label = f"{label} source {index}"
        if not isinstance(source, dict):
            raise CalendarBuildError(f"{path}: {source_label} must be a YAML object")
        validate_unknown_keys(path, source, SOURCE_KEYS, source_label)
        validate_source_dates(path, source, source_label)
        if "last_checked" in source:
            checked_dates.append(require_date(path, source, "last_checked", source_label))
    return tuple(checked_dates)


def validate_source_dates(path: Path, source: dict, label: str) -> None:
    for key in ("last_checked", "last_updated"):
        if key in source:
            require_date(path, source, key, label)


def optional_co_location(path: Path, data: dict, label: str) -> CoLocation:
    value = data.get("co_located_with")
    if value is None:
        return CoLocation()
    if not isinstance(value, dict):
        raise CalendarBuildError(f"{path}: {label} field 'co_located_with' must be a YAML object")
    validate_unknown_keys(path, value, CO_LOCATED_KEYS, f"{label} co_located_with")

    group = require_slug(path, value, "group", f"{label} co_located_with")
    name = optional_str(value, "name")
    url = optional_url(path, value, "url", f"{label} co_located_with")
    series = optional_slug_list(path, value, "series")
    return CoLocation(group=group, name=name, url=url, series=series)


def deadline_history(path: Path, deadline: dict, label: str) -> tuple[str, tuple[str, ...]]:
    value = deadline.get("history", [])
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: {label} field 'history' must be a list of YAML objects")

    entries: list[str] = []
    urls: list[str] = []
    for index, history in enumerate(value, start=1):
        history_label = f"{label} history {index}"
        if not isinstance(history, dict):
            raise CalendarBuildError(f"{path}: {history_label} must be a YAML object")
        validate_unknown_keys(path, history, DEADLINE_HISTORY_KEYS, history_label)
        history_date = require_date(path, history, "date", history_label).isoformat()
        announced = optional_date(path, history, "announced", history_label)
        history_url = optional_url(path, history, "url", history_label)
        note = optional_str(history, "note")
        details = []
        if announced:
            details.append(f"announced {announced.isoformat()}")
        if note:
            details.append(note)
        if history_url:
            details.append(history_url)
            urls.append(history_url)
        if details:
            entries.append(f"{history_date} ({'; '.join(details)})")
        else:
            entries.append(history_date)

    return "; ".join(entries), tuple(urls)


def require_date(path: Path, data: dict, key: str, label: str) -> date:
    value = data.get(key)
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a valid YYYY-MM-DD date") from exc


def optional_date(path: Path, data: dict, key: str, label: str) -> date | None:
    if key not in data:
        return None
    return require_date(path, data, key, label)


def optional_coordinate(
    path: Path,
    data: dict,
    key: str,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, int | float):
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be a number")
    coordinate = float(value)
    if not minimum <= coordinate <= maximum:
        raise CalendarBuildError(f"{path}: {label} field {key!r} must be between {minimum} and {maximum}")
    return coordinate


def normalize_status(path: Path, status: str, label: str) -> str:
    normalized = status.lower()
    if normalized not in STATUS_MAP:
        raise CalendarBuildError(f"{path}: {label} status must be one of {', '.join(sorted(STATUS_MAP))}")
    return STATUS_MAP[normalized]


def validate_url_status(path: Path, value: str, label: str) -> str:
    normalized = value.lower()
    if normalized not in URL_STATUS_VALUES:
        raise CalendarBuildError(
            f"{path}: {label} must be one of {', '.join(sorted(URL_STATUS_VALUES))}"
        )
    return normalized


def validate_event_record(event_path: Path, event: dict) -> None:
    validate_unknown_keys(event_path, event, EVENT_KEYS)
    require_slug_list(event_path, event, "event_types", "event")
    optional_url(event_path, event, "url", "event")
    url_status = optional_str(event, "url_status", event_path, "event")
    if url_status:
        validate_url_status(event_path, url_status, "event url_status")
    optional_co_location(event_path, event, "event")
    for key in ("timezone", "city", "country", "venue", "address"):
        optional_str(event, key, event_path, "event")
    status = optional_str(event, "status", event_path, "event")
    if status:
        normalize_status(event_path, status, "event")
    source_urls(event_path, event, "event")
    source_checked_dates(event_path, event, "event")
    start = optional_date(event_path, event, "start", "event")
    end = optional_date(event_path, event, "end", "event")
    if (start is None) != (end is None):
        raise CalendarBuildError("start and end must be provided together")
    if start and end and end < start:
        raise CalendarBuildError("end must be on or after start")
    latitude = optional_coordinate(event_path, event, "latitude", "event", minimum=-90, maximum=90)
    longitude = optional_coordinate(event_path, event, "longitude", "event", minimum=-180, maximum=180)
    if (latitude is None) != (longitude is None):
        raise CalendarBuildError("latitude and longitude must be provided together")

    deadlines = event.get("deadlines", [])
    if deadlines is None:
        deadlines = []
    if not isinstance(deadlines, list):
        raise CalendarBuildError("event deadlines must be a list")

    for index, deadline in enumerate(deadlines, start=1):
        label = f"event deadline {index}"
        if not isinstance(deadline, dict):
            raise CalendarBuildError(f"{event_path}: {label} must be a YAML object")
        validate_unknown_keys(event_path, deadline, DEADLINE_KEYS, label)
        require_slug(event_path, deadline, "type", label)
        optional_url(event_path, deadline, "url", label)
        if optional_date(event_path, deadline, "date", label) is None:
            raise CalendarBuildError(f"{event_path}: {label} field 'date' is required")
        deadline_status = optional_str(deadline, "status")
        if deadline_status:
            normalize_status(event_path, deadline_status, label)
        source_urls(event_path, deadline, label)
        source_checked_dates(event_path, deadline, label)
        deadline_history(event_path, deadline, label)


def latest_date(values: Iterable[date]) -> date | None:
    dates = tuple(values)
    if not dates:
        return None
    return max(dates)
