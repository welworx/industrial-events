from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

from industrial_events.config import BuildConfig, load_build_config, load_yaml_mapping
from industrial_events.models import CalendarItem, SeriesMetadata, UndatedEvent
from industrial_events.validation import (
    DEADLINE_KEYS,
    RECURRENCE_VALUES,
    SERIES_METADATA_KEYS,
    SLUG_RE,
    CalendarBuildError,
    deadline_history,
    latest_date,
    normalize_status,
    optional_co_location,
    optional_coordinate,
    optional_date,
    optional_slug_list,
    optional_str,
    optional_url,
    require_date,
    require_slug,
    require_slug_list,
    require_str,
    source_checked_dates,
    source_urls,
    validate_event_record,
    validate_unknown_keys,
    validate_url_status,
)

LOGGER = logging.getLogger("build_site")


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
            proceedings_url = source_link_by_type(event, "proceedings")
            program_url = source_link_by_type(event, "program")
            undated.append(
                UndatedEvent(
                    series_slug=metadata.slug,
                    domain=metadata.domain,
                    categories=metadata.categories,
                    title=title,
                    url=optional_url(event_path, event, "url", f"event {event_index}") or metadata.website,
                    scope=event_scope_label(title),
                    location=undated_location(event),
                    source_url=source_url,
                    source_urls=event_sources,
                    proceedings_url=proceedings_url,
                    program_url=program_url,
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
    website_status = optional_str(data, "website_status", path, "top level")
    if website_status:
        validate_url_status(path, website_status, "top level website_status")

    return SeriesMetadata(
        path=path,
        domain=domain,
        series=require_str(path, data, "series"),
        slug=slug,
        description=require_str(path, data, "description"),
        recurrence=recurrence,
        categories=require_slug_list(path, data, "categories"),
        topics=optional_slug_list(path, data, "topics"),
        website=optional_url(path, data, "website"),
        sources=source_urls(path, data, "top level"),
        checked_dates=source_checked_dates(path, data, "top level"),
    )


def event_files(metadata_path: Path) -> list[Path]:
    return sorted(path for path in metadata_path.parent.glob("*.yaml") if path.name != "metadata.yaml")


def load_event_file(path: Path) -> dict:
    data = load_yaml(path)
    validate_event_record(path, data)
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
    url = optional_url(path, event, "url", f"event {event_index}") or metadata.website
    status = normalize_status(path, optional_str(event, "status") or "confirmed", f"event {event_index}")
    event_sources = unique_values((*metadata.sources, *source_urls(path, event, f"event {event_index}")))
    proceedings_url = source_link_by_type(event, "proceedings")
    program_url = source_link_by_type(event, "program")
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
        source_urls=event_sources,
        proceedings_url=proceedings_url,
        program_url=program_url,
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
        deadline_url = optional_url(path, deadline, "url", f"event {event_index} deadline {deadline_index}") or url
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
                source_urls=deadline_sources,
                proceedings_url="",
                program_url="",
                description=deadline_description,
                last_checked=deadline_last_checked,
                co_location_group=co_location.group,
                co_location_name=co_location.name,
                co_location_url=co_location.url,
                co_location_series=co_location.series,
            )
        )

    return items


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
    return load_yaml_mapping(path, error_type=CalendarBuildError, read_label="YAML file")


def unique_values(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def source_link_by_type(data: dict, source_type: str) -> str:
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        return ""
    for source in sources:
        if not isinstance(source, dict):
            continue
        if optional_str(source, "type").lower() != source_type:
            continue
        return optional_str(source, "url")
    return ""


def format_coordinates(latitude: float | None, longitude: float | None) -> str:
    if latitude is None and longitude is None:
        return ""
    if latitude is None or longitude is None:
        return "incomplete"
    return f"{latitude:.7f}, {longitude:.7f}"


def build_description(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in pairs if value)
