from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from industrial_events.config import BuildConfig, load_build_config

LOGGER = logging.getLogger("build_site")

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SERIES_METADATA_KEYS = {
    "series",
    "slug",
    "website",
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
RECURRENCE_VALUES = {"recurring", "one-off", "unknown"}
SUBMISSION_DEADLINE_KEYWORDS = ("abstract", "manuscript", "paper", "papers", "poster", "proposal", "submission")


class CalendarBuildError(Exception):
    pass


@dataclass(frozen=True)
class CalendarItem:
    uid: str
    summary: str
    start: date
    end_exclusive: date
    series: str
    series_slug: str
    domain: str
    categories: tuple[str, ...]
    topics: tuple[str, ...]
    country: str
    kind: str
    status: str
    event_types: tuple[str, ...] = ("conference",)
    location: str = ""
    city: str = ""
    venue: str = ""
    address: str = ""
    url: str = ""
    description: str = ""
    latitude: float | None = None
    longitude: float | None = None
    last_checked: date | None = None
    co_location_group: str = ""
    co_location_name: str = ""
    co_location_url: str = ""
    co_location_series: tuple[str, ...] = ()


@dataclass(frozen=True)
class UndatedEvent:
    series_slug: str
    domain: str
    categories: tuple[str, ...]
    title: str
    url: str
    scope: str
    location: str
    source_url: str
    last_checked: date | None = None
    co_location_group: str = ""
    event_types: tuple[str, ...] = ("conference",)


@dataclass(frozen=True)
class SeriesMetadata:
    path: Path
    domain: str
    series: str
    slug: str
    description: str
    recurrence: str
    categories: tuple[str, ...]
    topics: tuple[str, ...]
    website: str = ""
    sources: tuple[str, ...] = ()
    checked_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class CoLocation:
    group: str = ""
    name: str = ""
    url: str = ""
    series: tuple[str, ...] = ()

    @property
    def description(self) -> str:
        if not self.group:
            return ""
        if self.name:
            return f"{self.name} ({self.group})"
        return self.group


def load_items(source_dir: Path, config: BuildConfig | None = None) -> list[CalendarItem]:
    config = config or load_build_config()
    if not source_dir.exists():
        raise CalendarBuildError(f"source directory does not exist: {source_dir}")

    items: list[CalendarItem] = []
    seen_slugs: dict[str, Path] = {}
    metadata_files = sorted(series_metadata_files(source_dir))
    if not metadata_files:
        raise CalendarBuildError(f"{source_dir}: no event series metadata files found")
    LOGGER.info("Found %d event series metadata file(s)", len(metadata_files))

    for metadata_path in metadata_files:
        metadata = load_series_metadata_file(source_dir, metadata_path)
        if metadata.slug in seen_slugs:
            raise CalendarBuildError(
                f"{metadata.path}: duplicate slug {metadata.slug!r}; first used in {seen_slugs[metadata.slug]}"
            )
        seen_slugs[metadata.slug] = metadata.path

        event_paths = event_files(metadata_path)
        LOGGER.debug("Loading %d event file(s) for series %s", len(event_paths), metadata.slug)
        for index, event_path in enumerate(event_paths, start=1):
            event = load_event_file(event_path)
            items.extend(items_for_event(metadata, index, event_path, event, config))

    return sorted(items, key=lambda item: (item.start, item.series_slug, item.kind, item.summary))


def load_undated_events(source_dir: Path) -> list[UndatedEvent]:
    if not source_dir.exists():
        return []

    undated: list[UndatedEvent] = []
    for metadata_path in sorted(series_metadata_files(source_dir)):
        metadata = load_series_metadata_file(source_dir, metadata_path)
        for event_index, event_path in enumerate(event_files(metadata_path), start=1):
            event = load_event_file(event_path)
            start = optional_date(event_path, event, "start", f"event {event_index}")
            end = optional_date(event_path, event, "end", f"event {event_index}")
            if start is not None or end is not None:
                continue

            title = require_str(event_path, event, "name", f"event {event_index}")
            event_types = require_slug_list(event_path, event, "event_types", f"event {event_index}")
            co_location = optional_co_location(event_path, event, f"event {event_index}")
            event_sources = source_urls(event_path, event, f"event {event_index}")
            checked_dates = (*metadata.checked_dates, *source_checked_dates(event_path, event, f"event {event_index}"))
            source_url = first_value((*event_sources, *metadata.sources))
            undated.append(
                UndatedEvent(
                    series_slug=metadata.slug,
                    domain=metadata.domain,
                    categories=metadata.categories,
                    title=title,
                    url=optional_str(event, "url") or metadata.website,
                    scope=event_scope_label(title),
                    location=undated_location(event),
                    source_url=source_url,
                    last_checked=latest_date(checked_dates),
                    co_location_group=co_location.group,
                    event_types=event_types,
                )
            )

    return sorted(undated, key=lambda item: (item.title.lower(), item.url))


def series_metadata_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.glob("*/*/metadata.yaml"))


def load_series_metadata(source_dir: Path) -> list[SeriesMetadata]:
    return [load_series_metadata_file(source_dir, path) for path in series_metadata_files(source_dir)]


def load_series_metadata_file(source_dir: Path, path: Path) -> SeriesMetadata:
    relative_parts = path.relative_to(source_dir).parts
    if len(relative_parts) != 3 or relative_parts[2] != "metadata.yaml":
        raise CalendarBuildError(
            f"{path}: series metadata files must be at events/<domain>/<series-slug>/metadata.yaml"
        )
    domain, series_folder, _ = relative_parts
    if not SLUG_RE.fullmatch(domain):
        raise CalendarBuildError(f"{path}: domain folder must be a lowercase slug")
    if not SLUG_RE.fullmatch(series_folder):
        raise CalendarBuildError(f"{path}: series folder must be a lowercase slug")

    data = load_yaml(path)
    validate_unknown_keys(path, data, SERIES_METADATA_KEYS)
    slug = require_slug(path, data, "slug")
    if slug != series_folder:
        raise CalendarBuildError(f"{path}: metadata slug must match the series folder name")

    recurrence = optional_str(data, "recurrence") or "unknown"
    if recurrence not in RECURRENCE_VALUES:
        raise CalendarBuildError(f"{path}: recurrence must be one of {', '.join(sorted(RECURRENCE_VALUES))}")

    return SeriesMetadata(
        path=path,
        domain=domain,
        series=require_str(path, data, "series"),
        slug=slug,
        description=require_str(path, data, "description"),
        recurrence=recurrence,
        categories=require_slug_list(path, data, "categories"),
        topics=optional_slug_list(path, data, "topics"),
        website=optional_str(data, "website"),
        sources=source_urls(path, data, "top level"),
        checked_dates=source_checked_dates(path, data, "top level"),
    )


def event_files(metadata_path: Path) -> list[Path]:
    return sorted(path for path in metadata_path.parent.glob("*.yaml") if path.name != "metadata.yaml")


def load_event_file(path: Path) -> dict:
    data = load_yaml(path)
    validate_unknown_keys(path, data, EVENT_KEYS)
    require_slug_list(path, data, "event_types", "event")
    return data


def items_for_event(
    metadata: SeriesMetadata,
    event_index: int,
    path: Path,
    event: dict,
    config: BuildConfig,
) -> list[CalendarItem]:
    event_name = require_str(path, event, "name", f"event {event_index}")
    event_types = require_slug_list(path, event, "event_types", f"event {event_index}")
    start = optional_date(path, event, "start", f"event {event_index}")
    end = optional_date(path, event, "end", f"event {event_index}")
    if start is None and end is None:
        status = optional_str(event, "status")
        if status not in {"estimated", "tentative"}:
            raise CalendarBuildError(
                f"{path}: event {event_index} start and end are required unless status is estimated or tentative"
            )
        source_urls(path, event, f"event {event_index}")
        return []
    if start is None or end is None:
        raise CalendarBuildError(f"{path}: event {event_index} start and end must be provided together")
    if end < start:
        raise CalendarBuildError(f"{path}: event {event_index} end must be on or after start")

    country = optional_str(event, "country").lower()
    city = optional_str(event, "city")
    venue = optional_str(event, "venue")
    address = optional_str(event, "address")
    latitude = optional_coordinate(path, event, "latitude", f"event {event_index}", minimum=-90, maximum=90)
    longitude = optional_coordinate(path, event, "longitude", f"event {event_index}", minimum=-180, maximum=180)
    if (latitude is None) != (longitude is None):
        raise CalendarBuildError(f"{path}: event {event_index} latitude and longitude must be provided together")
    co_location = optional_co_location(path, event, f"event {event_index}")
    coordinates = format_coordinates(latitude, longitude)
    location = ", ".join(part for part in (venue, address, city, country.upper()) if part)
    url = optional_str(event, "url") or metadata.website
    status = normalize_status(path, optional_str(event, "status") or "confirmed", f"event {event_index}")
    event_sources = unique_values((*metadata.sources, *source_urls(path, event, f"event {event_index}")))
    event_checked_dates = (*metadata.checked_dates, *source_checked_dates(path, event, f"event {event_index}"))
    event_last_checked = latest_date(event_checked_dates)
    description = build_description(
        [
            ("Series", metadata.series),
            ("Kind", "event"),
            ("Event types", ", ".join(event_types)),
            ("Categories", ", ".join(metadata.categories)),
            ("Topics", ", ".join(metadata.topics)),
            ("Website", url),
            ("Address", address),
            ("Coordinates", coordinates),
            ("Co-located group", co_location.description),
            ("Co-located series", ", ".join(co_location.series)),
            ("Co-located URL", co_location.url),
            ("Disclaimer", config.disclaimer),
            ("Sources", ", ".join(event_sources)),
        ]
    )

    event_uid_prefix = f"{metadata.slug}-{path.stem}"
    event_item = CalendarItem(
        uid=f"{event_uid_prefix}@{config.uid_domain}",
        summary=event_name,
        start=start,
        end_exclusive=end + timedelta(days=1),
        series=metadata.series,
        series_slug=metadata.slug,
        domain=metadata.domain,
        categories=metadata.categories,
        topics=metadata.topics,
        country=country,
        kind="event",
        status=status,
        event_types=event_types,
        location=location,
        city=city,
        venue=venue,
        address=address,
        url=url,
        description=description,
        latitude=latitude,
        longitude=longitude,
        last_checked=event_last_checked,
        co_location_group=co_location.group,
        co_location_name=co_location.name,
        co_location_url=co_location.url,
        co_location_series=co_location.series,
    )

    items = [event_item]
    deadlines = event.get("deadlines", [])
    if deadlines is None:
        deadlines = []
    if not isinstance(deadlines, list):
        raise CalendarBuildError(f"{path}: event {event_index} deadlines must be a list of YAML objects")

    for deadline_index, deadline in enumerate(deadlines, start=1):
        if not isinstance(deadline, dict):
            raise CalendarBuildError(f"{path}: event {event_index} deadline {deadline_index} must be a YAML object")
        validate_unknown_keys(path, deadline, DEADLINE_KEYS, f"event {event_index} deadline {deadline_index}")
        deadline_type = require_slug(path, deadline, "type", f"event {event_index} deadline {deadline_index}")
        deadline_date = require_date(path, deadline, "date", f"event {event_index} deadline {deadline_index}")
        deadline_name = optional_str(deadline, "name") or f"{deadline_type.replace('-', ' ').title()} deadline"
        deadline_status = normalize_status(
            path,
            optional_str(deadline, "status") or "confirmed",
            f"event {event_index} deadline {deadline_index}",
        )
        deadline_url = optional_str(deadline, "url") or url
        history_summary, history_urls = deadline_history(
            path,
            deadline,
            f"event {event_index} deadline {deadline_index}",
        )
        deadline_sources = unique_values(
            (
                *event_sources,
                *source_urls(path, deadline, f"event {event_index} deadline {deadline_index}"),
                *history_urls,
            )
        )
        deadline_last_checked = latest_date(
            (
                *event_checked_dates,
                *source_checked_dates(path, deadline, f"event {event_index} deadline {deadline_index}"),
            )
        )
        deadline_summary = f"{metadata.series}: {deadline_name}"
        deadline_description = build_description(
            [
                ("Series", metadata.series),
                ("Event", event_name),
                ("Kind", f"{deadline_type} deadline"),
                ("Event types", ", ".join(event_types)),
                ("Categories", ", ".join(metadata.categories)),
                ("Topics", ", ".join(metadata.topics)),
                ("Website", deadline_url),
                ("Deadline history", history_summary),
                ("Co-located group", co_location.description),
                ("Co-located series", ", ".join(co_location.series)),
                ("Co-located URL", co_location.url),
                ("Disclaimer", config.disclaimer),
                ("Sources", ", ".join(deadline_sources)),
            ]
        )
        items.append(
            CalendarItem(
                uid=f"{event_uid_prefix}-deadline-{deadline_index}-{deadline_type}@{config.uid_domain}",
                summary=deadline_summary,
                start=deadline_date,
                end_exclusive=deadline_date + timedelta(days=1),
                series=metadata.series,
                series_slug=metadata.slug,
                domain=metadata.domain,
                categories=metadata.categories,
                topics=metadata.topics,
                country=country,
                kind=f"deadline-{deadline_type}",
                status=deadline_status,
                event_types=event_types,
                location="",
                url=deadline_url,
                description=deadline_description,
                last_checked=deadline_last_checked,
                co_location_group=co_location.group,
                co_location_name=co_location.name,
                co_location_url=co_location.url,
                co_location_series=co_location.series,
            )
        )

    return items


def latest_date(values: Iterable[date]) -> date | None:
    dates = tuple(values)
    if not dates:
        return None
    return max(dates)


def first_value(values: Iterable[str]) -> str:
    return next((value for value in values if value), "")


def undated_location(event: dict) -> str:
    country = optional_str(event, "country").upper()
    return ", ".join(part for part in (optional_str(event, "venue"), optional_str(event, "city"), country) if part)


def event_scope_label(title: str) -> str:
    parenthetical = re.search(r"\(([^()]+)\)\s*$", title)
    if not parenthetical:
        return ""
    return parenthetical.group(1)


def common_value(values: Iterable[str]) -> str:
    unique = unique_values(value for value in values if value)
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return ""
    return "; ".join(unique)


def source_files(source_dir: Path) -> list[Path]:
    return sorted((*source_dir.rglob("*.yaml"), *source_dir.rglob("*.yml")))


def load_yaml(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise CalendarBuildError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CalendarBuildError(f"{path}: expected a YAML object")
    return data


def validate_unknown_keys(path: Path, data: dict, allowed: set[str], label: str = "top level") -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise CalendarBuildError(f"{path}: unknown {label} field(s): {', '.join(unknown)}")


def require_str(path: Path, data: dict, key: str, label: str = "top level") -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CalendarBuildError(f"{path}: {label} field {key!r} is required")
    return value.strip()


def optional_str(data: dict, key: str) -> str:
    value = data.get(key, "")
    if isinstance(value, str):
        return value.strip()
    return ""


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
        urls.append(require_str(path, source, "url", source_label))
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
    url = optional_str(value, "url")
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
        history_url = optional_str(history, "url")
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


def unique_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


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


def format_coordinates(latitude: float | None, longitude: float | None) -> str:
    if latitude is None and longitude is None:
        return ""
    if latitude is None or longitude is None:
        return "incomplete"
    return f"{latitude:.7f}, {longitude:.7f}"


def normalize_status(path: Path, status: str, label: str) -> str:
    normalized = status.lower()
    if normalized not in STATUS_MAP:
        raise CalendarBuildError(f"{path}: {label} status must be one of {', '.join(sorted(STATUS_MAP))}")
    return STATUS_MAP[normalized]


def build_description(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in pairs if value)
