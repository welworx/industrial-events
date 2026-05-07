from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from calendar import month_name
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
DEFAULT_SOURCE_DIR = ROOT / "events"
DEFAULT_OUTPUT_DIR = ROOT / "public" / "calendars"
PRODID = "-//Industrial Events//EN"
DTSTAMP = "20260504T000000Z"
SITE_URL = "https://welworx.github.io/industrial-events/"
REPOSITORY_URL = "https://github.com/welworx/industrial-events"
RSS_UPDATED_ENV = "INDUSTRIAL_EVENTS_FEED_UPDATED"
DEFAULT_FEED_UPDATED = datetime(2026, 5, 4, tzinfo=UTC)
README_UPCOMING_START = "<!-- generated:upcoming-events:start -->"
README_UPCOMING_END = "<!-- generated:upcoming-events:end -->"
README_SUBMISSION_START = "<!-- generated:submission-opportunities:start -->"
README_SUBMISSION_END = "<!-- generated:submission-opportunities:end -->"
README_SERIES_START = "<!-- generated:series-overview:start -->"
README_SERIES_END = "<!-- generated:series-overview:end -->"
README_SOURCES_START = "<!-- generated:overview-sources:start -->"
README_SOURCES_END = "<!-- generated:overview-sources:end -->"
DISCLAIMER = (
    "This calendar makes existing public event information easier to access. "
    "It may be incomplete, outdated, or wrong. Always verify important dates and details "
    "against the official event source. The maintainer is not responsible for missing "
    "updates, incorrect information, missed deadlines, travel costs, registration decisions, "
    "or any other consequence of using this feed."
)
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
class Feed:
    path: Path
    name: str
    items: tuple[CalendarItem, ...]


@dataclass(frozen=True)
class UndatedConference:
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
class MarkdownPage:
    path: Path
    title: str
    items: tuple[CalendarItem, ...]
    undated_conferences: tuple[UndatedConference, ...] = ()


ConferenceMarkdownRow = tuple[
    date,
    date,
    str,
    str,
    str,
    date | None,
    tuple[CalendarItem, ...],
    tuple[CalendarItem, ...],
]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GitHub Pages outputs from event YAML files.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        feeds = build_site(args.source, args.output)
    except CalendarBuildError as exc:
        LOGGER.error("Build failed: %s", exc)
        return 1

    LOGGER.info("Generated %d calendar feed(s) in %s", len(feeds), args.output)
    return 0


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(levelname)s %(message)s", force=True)


def build_site(
    source_dir: Path,
    output_dir: Path,
    updated_at: datetime | None = None,
    reference_date: date | None = None,
) -> list[Feed]:
    LOGGER.info("Building event outputs")
    LOGGER.info("Source directory: %s", source_dir)
    LOGGER.info("Output directory: %s", output_dir)
    updated_at = feed_updated_at() if updated_at is None else normalize_datetime(updated_at)
    LOGGER.info("RSS build timestamp: %s", updated_at.isoformat())
    items = load_items(source_dir)
    event_count = sum(1 for item in items if item.kind == "conference")
    deadline_count = len(items) - event_count
    LOGGER.info(
        "Loaded %d calendar item(s): %d event(s), %d deadline(s)",
        len(items),
        event_count,
        deadline_count,
    )
    feeds = build_feeds(items, output_dir)
    LOGGER.info("Built %d iCalendar feed definition(s)", len(feeds))
    undated_conferences = load_undated_conferences(source_dir)
    LOGGER.info("Loaded %d announced event(s) without calendar dates", len(undated_conferences))
    stale_count = clean_stale_feeds(output_dir, {feed.path.resolve() for feed in feeds})
    LOGGER.info("Cleaned %d stale iCalendar feed(s)", stale_count)
    output_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Writing %d iCalendar feed file(s)", len(feeds))
    for feed in feeds:
        feed.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing iCalendar feed: %s (%d item(s))", feed.path, len(feed.items))
        feed.path.write_text(render_calendar(feed.name, feed.items), encoding="utf-8", newline="\n")

    LOGGER.info("Writing calendar feed index")
    write_index(output_dir, feeds)
    LOGGER.info("Writing event list pages")
    write_conference_markdown(output_dir.parent, items, undated_conferences, reference_date)
    LOGGER.info("Updating README overview sections")
    write_readme_overview(
        output_dir.parent.parent / "README.md",
        source_dir,
        items,
        undated_conferences,
        reference_date,
    )
    LOGGER.info("Writing RSS event stream")
    write_rss_feed(output_dir.parent, items, updated_at)
    LOGGER.info("Writing site index page")
    write_site_index(output_dir, feeds)
    return feeds


def feed_updated_at() -> datetime:
    env_value = os.environ.get(RSS_UPDATED_ENV, "").strip()
    if env_value:
        return parse_datetime(env_value, RSS_UPDATED_ENV)

    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cI"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return DEFAULT_FEED_UPDATED

    value = completed.stdout.strip()
    if not value:
        return DEFAULT_FEED_UPDATED
    return parse_datetime(value, "git commit date")


def parse_datetime(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarBuildError(f"{label} must be an ISO 8601 date-time") from exc
    return normalize_datetime(parsed)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def load_items(source_dir: Path) -> list[CalendarItem]:
    if not source_dir.exists():
        LOGGER.warning("Source directory does not exist: %s", source_dir)
        return []

    items: list[CalendarItem] = []
    seen_slugs: dict[str, Path] = {}
    metadata_files = sorted(series_metadata_files(source_dir))
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
            items.extend(items_for_event(metadata, index, event_path, event))

    return sorted(items, key=lambda item: (item.start, item.series_slug, item.kind, item.summary))


def load_undated_conferences(source_dir: Path) -> list[UndatedConference]:
    if not source_dir.exists():
        return []

    undated: list[UndatedConference] = []
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
                UndatedConference(
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
            ("Kind", "conference"),
            ("Event types", ", ".join(event_types)),
            ("Categories", ", ".join(metadata.categories)),
            ("Topics", ", ".join(metadata.topics)),
            ("Website", url),
            ("Address", address),
            ("Coordinates", coordinates),
            ("Co-located group", co_location.description),
            ("Co-located series", ", ".join(co_location.series)),
            ("Co-located URL", co_location.url),
            ("Disclaimer", DISCLAIMER),
            ("Sources", ", ".join(event_sources)),
        ]
    )

    event_item = CalendarItem(
        uid=f"{metadata.slug}-{start.isoformat()}-event@industrial-events",
        summary=event_name,
        start=start,
        end_exclusive=end + timedelta(days=1),
        series=metadata.series,
        series_slug=metadata.slug,
        domain=metadata.domain,
        categories=metadata.categories,
        topics=metadata.topics,
        country=country,
        kind="conference",
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
                ("Disclaimer", DISCLAIMER),
                ("Sources", ", ".join(deadline_sources)),
            ]
        )
        items.append(
            CalendarItem(
                uid=f"{metadata.slug}-{start.isoformat()}-{deadline_type}-{deadline_date.isoformat()}@industrial-events",
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


def build_feeds(items: Iterable[CalendarItem], output_dir: Path) -> list[Feed]:
    items_tuple = tuple(items)
    feeds = [Feed(output_dir / "all.ics", "All Events", items_tuple)]

    by_series: dict[str, list[CalendarItem]] = {}
    by_category: dict[str, list[CalendarItem]] = {}
    by_event_type: dict[str, list[CalendarItem]] = {}
    by_country: dict[str, list[CalendarItem]] = {}
    by_domain: dict[str, list[CalendarItem]] = {}
    by_group: dict[str, list[CalendarItem]] = {}

    for item in items_tuple:
        by_series.setdefault(item.series_slug, []).append(item)
        if item.country:
            by_country.setdefault(item.country, []).append(item)
        by_domain.setdefault(item.domain, []).append(item)
        if item.co_location_group:
            by_group.setdefault(item.co_location_group, []).append(item)
        for category in item.categories:
            by_category.setdefault(category, []).append(item)
        for event_type in item.event_types:
            by_event_type.setdefault(event_type, []).append(item)

    for slug, feed_items in sorted(by_series.items()):
        feeds.append(Feed(output_dir / "series" / f"{slug}.ics", f"Event Series: {slug}", tuple(feed_items)))
    for category, feed_items in sorted(by_category.items()):
        feeds.append(
            Feed(output_dir / "category" / f"{category}.ics", f"Event Category: {category}", tuple(feed_items))
        )
    for event_type, feed_items in sorted(by_event_type.items()):
        feeds.append(
            Feed(
                output_dir / "event-type" / f"{event_type}.ics",
                f"Event Type: {event_type}",
                tuple(feed_items),
            )
        )
    for country, feed_items in sorted(by_country.items()):
        feeds.append(
            Feed(
                output_dir / "country" / f"{country}.ics",
                f"Event Country: {country.upper()}",
                tuple(feed_items),
            )
        )
    for domain, feed_items in sorted(by_domain.items()):
        feeds.append(Feed(output_dir / "domain" / f"{domain}.ics", f"Event Domain: {domain}", tuple(feed_items)))
    for group, feed_items in sorted(by_group.items()):
        feeds.append(Feed(output_dir / "group" / f"{group}.ics", f"Co-located Group: {group}", tuple(feed_items)))

    return feeds


def render_calendar(name: str, items: Iterable[CalendarItem]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:UTC",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        f"X-WR-CALNAME:{escape_text(name)}",
        f"X-WR-CALDESC:{escape_text(DISCLAIMER)}",
    ]

    for item in items:
        lines.extend(render_event(item))

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def render_event(item: CalendarItem) -> list[str]:
    tags = unique_values(
        tag
        for tag in (
            "conference",
            item.kind,
            item.series_slug,
            item.country,
            *item.event_types,
            *item.categories,
            *item.topics,
        )
        if tag
    )
    lines = [
        "BEGIN:VEVENT",
        f"UID:{escape_text(item.uid)}",
        f"DTSTAMP:{DTSTAMP}",
        f"CREATED:{DTSTAMP}",
        f"LAST-MODIFIED:{DTSTAMP}",
        "SEQUENCE:0",
        "CLASS:PUBLIC",
        f"DTSTART;VALUE=DATE:{format_date(item.start)}",
        f"DTEND;VALUE=DATE:{format_date(item.end_exclusive)}",
        f"SUMMARY:{escape_text(item.summary)}",
        f"STATUS:{item.status}",
        "TRANSP:OPAQUE",
        f"CATEGORIES:{','.join(escape_text(tag) for tag in tags)}",
        f"X-EVENT-SERIES:{escape_text(item.series)}",
        f"X-EVENT-SERIES-SLUG:{escape_text(item.series_slug)}",
        f"X-EVENT-DOMAIN:{escape_text(item.domain)}",
        f"X-EVENT-TYPES:{','.join(escape_text(event_type) for event_type in item.event_types)}",
    ]
    if item.country:
        lines.append(f"X-EVENT-COUNTRY:{escape_text(item.country.upper())}")
    if item.location:
        lines.append(f"LOCATION:{escape_text(item.location)}")
    if item.latitude is not None and item.longitude is not None:
        lines.append(f"GEO:{item.latitude:.7f};{item.longitude:.7f}")
    if item.co_location_group:
        lines.append(f"X-EVENT-COLOCATED-GROUP:{escape_text(item.co_location_group)}")
    if item.co_location_series:
        lines.append(f"X-EVENT-COLOCATED-SERIES:{','.join(escape_text(slug) for slug in item.co_location_series)}")
    if item.url:
        lines.append(f"URL:{escape_text(item.url)}")
    if item.description:
        lines.append(f"DESCRIPTION:{escape_text(item.description)}")
    lines.append("END:VEVENT")
    return lines


def latest_date(values: Iterable[date]) -> date | None:
    dates = tuple(values)
    if not dates:
        return None
    return max(dates)


def write_index(output_dir: Path, feeds: list[Feed]) -> None:
    index = [
        {
            "name": feed.name,
            "path": feed.path.relative_to(output_dir).as_posix(),
            "items": len(feed.items),
        }
        for feed in feeds
    ]
    (output_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def write_conference_markdown(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference] = (),
    reference_date: date | None = None,
) -> None:
    pages = conference_markdown_pages(site_root, items, undated_conferences)
    remove_legacy_conference_markdown(site_root)
    expected_paths = {page.path.resolve() for page in pages} | {
        page.path.with_suffix(".html").resolve() for page in pages
    }
    cleaned = clean_stale_conference_docs(site_root, expected_paths)
    LOGGER.info("Cleaned %d stale event doc file(s)", cleaned)
    LOGGER.info("Writing %d event doc page(s)", len(pages))
    for page in pages:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing event Markdown file: %s", page.path)
        page.path.write_text(
            render_conference_markdown(
                page.items,
                page.undated_conferences,
                reference_date,
                title=page.title,
            ),
            encoding="utf-8",
            newline="\n",
        )
        html_path = page.path.with_suffix(".html")
        LOGGER.debug("Writing event HTML file: %s", html_path)
        html_path.write_text(
            render_conference_html(
                page.items,
                page.undated_conferences,
                reference_date,
                title=page.title,
            ),
            encoding="utf-8",
            newline="\n",
        )


def write_readme_overview(
    readme_path: Path,
    source_dir: Path,
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference],
    reference_date: date | None = None,
) -> None:
    if not readme_path.exists():
        LOGGER.debug("Skipping README update; file does not exist: %s", readme_path)
        return

    items_tuple = tuple(items)
    undated_tuple = tuple(undated_conferences)
    current = readme_path.read_text(encoding="utf-8")
    updated = replace_marked_section(
        current,
        README_UPCOMING_START,
        README_UPCOMING_END,
        render_readme_upcoming_events(items_tuple, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SUBMISSION_START,
        README_SUBMISSION_END,
        render_readme_submission_opportunities(items_tuple, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SERIES_START,
        README_SERIES_END,
        render_readme_series_overview(
            load_series_metadata(source_dir),
            items_tuple,
            undated_tuple,
            reference_date,
        ),
    )
    updated = replace_marked_section(
        updated,
        README_SOURCES_START,
        README_SOURCES_END,
        render_readme_overview_sources(ROOT / "sources"),
    )
    if updated == current:
        return
    readme_path.write_text(updated, encoding="utf-8", newline="\n")


def render_readme_upcoming_events(
    items: Iterable[CalendarItem],
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    rows = [row for row in conference_markdown_rows(items) if row[0] >= today]
    lines = [
        README_UPCOMING_START,
        "",
        "Full list: [All upcoming events](https://welworx.github.io/industrial-events/events/all.html#upcoming-events).",
        "",
    ]
    if not rows:
        lines.append("No tracked upcoming events.")
    else:
        current_year: int | None = None
        current_month: int | None = None
        for start, end_exclusive, title, url, location, _last_checked, conferences, deadlines in sorted(
            rows,
            key=lambda row: (row[0], row[2].lower()),
        ):
            if start.year != current_year:
                if current_year is not None:
                    lines.append("")
                current_year = start.year
                current_month = None
                lines.extend([f"### {start.year}", ""])
            if start.month != current_month:
                if current_month is not None:
                    lines.append("")
                current_month = start.month
                lines.extend([f"#### {month_name[start.month]}", ""])
            event_details = " ".join(
                part
                for part in (
                    location_badge(conferences, location),
                    venue_badge(conferences, today),
                    readme_event_badges(conferences, deadlines, start, today),
                    limited_tag_list(common_tags(conferences)),
                )
                if part
            )
            lines.append(
                "- "
                f"{compact_date_range(start, end_exclusive)}: "
                f"{conference_event_cell(title, url, conferences)} "
                f"{event_details}"
            )
    lines.extend(["", README_UPCOMING_END])
    return "\n".join(lines)


def render_readme_submission_opportunities(
    items: Iterable[CalendarItem],
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    opportunities = submission_opportunity_rows(conference_markdown_rows(items), today)
    lines = [
        README_SUBMISSION_START,
        "",
        "Full list: "
        "[All submission opportunities]"
        "(https://welworx.github.io/industrial-events/events/all.html#submission-opportunities).",
        "",
    ]
    if not opportunities:
        lines.append("No tracked events with open submission deadlines.")
    else:
        lines.extend(
            [
                "| Deadline | Event | Event Dates | Scope / Co-located | Last Checked |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for deadline, row in opportunities:
            start, end_exclusive, title, url, _location, last_checked, conferences, _deadlines = row
            lines.append(
                "| "
                f"{markdown_link(submission_deadline_label(deadline, conferences), deadline.url)} | "
                f"{markdown_link(title, url)} | "
                f"{escape_markdown_table(compact_date_range_with_year(start, end_exclusive))} | "
                f"{conference_scope_cell(title, conferences)} | "
                f"{format_last_checked(last_checked)} |"
            )
    lines.extend(["", README_SUBMISSION_END])
    return "\n".join(lines)


def render_readme_series_overview(
    metadata: Iterable[SeriesMetadata],
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference],
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_conferences)
    lines = [
        README_SERIES_START,
        "",
    ]
    for series in sorted(metadata, key=lambda value: value.series.lower()):
        series_items = tuple(item for item in items_tuple if item.series_slug == series.slug)
        series_undated = tuple(item for item in undated_tuple if item.series_slug == series.slug)
        lines.extend(
            [
                f"- **{markdown_link(series.series, series_page_url(series.slug))}**{official_series_link(series)}",
                f"  {series.description}",
                f"  **Next:** {next_series_event_cell(series_items, series_undated, today)}",
                "",
            ]
        )
    lines.append(README_SERIES_END)
    return "\n".join(lines)


def render_readme_overview_sources(source_dir: Path) -> str:
    sources = load_source_pages(source_dir)
    lines = [
        README_SOURCES_START,
        "",
        "Discovery sources help find and monitor events. "
        "Event-specific verification dates are tracked with the event data.",
        "",
    ]
    if not sources:
        lines.append("No discovery sources are tracked yet.")
    else:
        for source in sources:
            lines.append(f"- {markdown_link(source['name'], source['url'])}")
    lines.extend(["", README_SOURCES_END])
    return "\n".join(lines)


def replace_marked_section(content: str, start_marker: str, end_marker: str, section: str) -> str:
    marker_start = content.find(start_marker)
    marker_end = content.find(end_marker, marker_start if marker_start != -1 else 0)
    if marker_start == -1 or marker_end == -1:
        return content
    end = marker_end + len(end_marker)
    return content[:marker_start] + section + content[end:]


def tag_list(values: Iterable[str]) -> str:
    tags = unique_values(value for value in values if value)
    if not tags:
        return "TBD"
    return " ".join(f"`{escape_markdown_table(tag)}`" for tag in tags)


def limited_tag_list(values: Iterable[str], limit: int = 6) -> str:
    tags = unique_values(value for value in values if value)
    visible = tags[:limit]
    result = " ".join(f"`{escape_markdown_table(tag)}`" for tag in visible)
    if len(tags) > limit:
        result = f"{result} +{len(tags) - limit} more"
    return result or "TBD"


def readme_event_badges(
    conferences: tuple[CalendarItem, ...],
    deadlines: tuple[CalendarItem, ...],
    event_start: date,
    reference_date: date,
) -> str:
    badges = [event_type_badge(event_type) for event_type in common_event_types(conferences)]
    badges.append(cfp_badge(deadlines, event_start, reference_date))
    return " ".join(badges)


def venue_badge(conferences: tuple[CalendarItem, ...], reference_date: date) -> str:
    if any(not item.venue and item.start >= reference_date for item in conferences):
        return shield_badge("venue", "TBD", "yellow")
    return ""


def event_type_badge(event_type: str) -> str:
    colors = {
        "conference": "blue",
        "exhibition": "teal",
        "trade-fair": "orange",
    }
    return shield_badge("type", event_type, colors.get(event_type, "lightgrey"))


def cfp_badge(deadlines: tuple[CalendarItem, ...], event_start: date, reference_date: date) -> str:
    open_deadlines = tuple(deadline for deadline in deadlines if deadline.start >= reference_date)
    if open_deadlines:
        deadline = min(open_deadlines, key=lambda item: item.start)
        return shield_badge("CFP", f"due {deadline.start.isoformat()}", "brightgreen")
    if deadlines:
        deadline = max(deadlines, key=lambda item: item.start)
        return shield_badge("CFP", f"closed {deadline.start.isoformat()}", "lightgrey")
    if event_start >= reference_date:
        return shield_badge("CFP", "TBD", "yellow")
    return shield_badge("CFP", "closed", "lightgrey")


def shield_badge(label: str, message: str, color: str) -> str:
    alt = f"{label}: {message}"
    return (
        f"![{escape_markdown_image_alt(alt)}]"
        f"(https://img.shields.io/badge/{shield_path_part(label)}-{shield_path_part(message)}-{color})"
    )


def location_badge(conferences: tuple[CalendarItem, ...], fallback_location: str) -> str:
    label = compact_location(conferences, fallback_location)
    badge = shield_badge("location", label, "informational")
    if not fallback_location:
        return badge
    maps_url = google_maps_url(fallback_location, conferences)
    return f"[{badge}](<{maps_url}>)"


def shield_path_part(value: str) -> str:
    return quote(value.replace("-", "--"), safe="")


def escape_markdown_image_alt(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def compact_date_range(start: date, end_exclusive: date) -> str:
    end_inclusive = end_exclusive - timedelta(days=1)
    if start == end_inclusive:
        return str(start.day)
    if start.year == end_inclusive.year and start.month == end_inclusive.month:
        return f"{start.day}-{end_inclusive.day}"
    return f"{start:%b} {start.day}-{end_inclusive:%b} {end_inclusive.day}"


def compact_date_range_with_year(start: date, end_exclusive: date) -> str:
    end_inclusive = end_exclusive - timedelta(days=1)
    if start == end_inclusive:
        return f"{start:%b} {start.day}, {start.year}"
    if start.year == end_inclusive.year and start.month == end_inclusive.month:
        return f"{start:%b} {start.day}-{end_inclusive.day}, {start.year}"
    if start.year == end_inclusive.year:
        return f"{start:%b} {start.day}-{end_inclusive:%b} {end_inclusive.day}, {start.year}"
    return f"{start:%b} {start.day}, {start.year}-{end_inclusive:%b} {end_inclusive.day}, {end_inclusive.year}"


def compact_location(conferences: tuple[CalendarItem, ...], fallback_location: str) -> str:
    venue = common_value(item.venue for item in conferences)
    city = common_value(item.city for item in conferences)
    country = common_value(item.country.upper() for item in conferences if item.country)
    city_country = ", ".join(part for part in (city, country) if part)
    if city_country:
        return escape_markdown_table(city_country)
    if venue:
        return escape_markdown_table(venue)
    return escape_markdown_table(fallback_location or "Location TBD")


def common_event_types(conferences: Iterable[CalendarItem]) -> tuple[str, ...]:
    return unique_values(event_type for item in conferences for event_type in item.event_types)


def common_tags(conferences: Iterable[CalendarItem]) -> tuple[str, ...]:
    return unique_values(tag for item in conferences for tag in (*item.categories, *item.topics))


def next_series_event_cell(
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference],
    reference_date: date,
) -> str:
    upcoming = sorted(
        (item for item in items if item.kind == "conference" and item.start >= reference_date),
        key=lambda item: (item.start, item.summary.lower()),
    )
    if upcoming:
        item = upcoming[0]
        return (
            f"{shield_badge('next', compact_date_range_with_year(item.start, item.end_exclusive), 'brightgreen')} "
            f"{markdown_link(item.summary, item.url)}"
        )
    undated = sorted(undated_conferences, key=lambda item: item.title.lower())
    if undated:
        return f"{shield_badge('next', 'TBD', 'yellow')} {markdown_link(undated[0].title, undated[0].url)}"
    return shield_badge("next", "TBD", "yellow")


def series_page_url(slug: str) -> str:
    return f"{SITE_URL}events/series/{slug}.html"


def official_series_link(series: SeriesMetadata) -> str:
    official_url = series.website or next((url for url in series.sources if url), "")
    if not official_url:
        return ""
    return f" {markdown_link('↗', official_url)}"


def load_source_pages(source_dir: Path) -> list[dict]:
    if not source_dir.exists():
        return []

    sources: list[dict] = []
    for path in sorted(source_dir.glob("*/*.yaml")):
        data = load_yaml(path)
        validate_unknown_keys(path, data, SOURCE_PAGE_KEYS)
        sources.append(
            {
                "name": require_str(path, data, "name"),
                "slug": require_slug(path, data, "slug"),
                "url": require_str(path, data, "url"),
                "type": require_slug(path, data, "type"),
                "categories": optional_slug_list(path, data, "categories"),
                "topics": optional_slug_list(path, data, "topics"),
                "last_checked": optional_date(path, data, "last_checked", "top level"),
                "last_updated": optional_date(path, data, "last_updated", "top level"),
                "note": optional_str(data, "note"),
            }
        )
    return sources


def conference_markdown_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference],
) -> tuple[MarkdownPage, ...]:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_conferences)
    pages = [MarkdownPage(site_root / "events" / "all.md", "Events", items_tuple, undated_tuple)]

    pages.extend(
        markdown_split_pages(
            site_root,
            "series",
            "Event Series",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: (item.series_slug,),
            undated_keys=lambda item: (item.series_slug,),
        )
    )
    pages.extend(
        markdown_split_pages(
            site_root,
            "category",
            "Event Category",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: item.categories,
            undated_keys=lambda item: item.categories,
        )
    )
    pages.extend(
        markdown_split_pages(
            site_root,
            "event-type",
            "Event Type",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: item.event_types,
            undated_keys=lambda item: item.event_types,
        )
    )
    pages.extend(
        markdown_split_pages(
            site_root,
            "domain",
            "Event Domain",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: (item.domain,),
            undated_keys=lambda item: (item.domain,),
        )
    )
    pages.extend(
        markdown_split_pages(
            site_root,
            "group",
            "Co-located Group",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: (item.co_location_group,) if item.co_location_group else (),
            undated_keys=lambda item: (item.co_location_group,) if item.co_location_group else (),
        )
    )
    return tuple(pages)


def markdown_split_pages(
    site_root: Path,
    split: str,
    title_prefix: str,
    items: tuple[CalendarItem, ...],
    undated_conferences: tuple[UndatedConference, ...],
    *,
    item_keys: Callable[[CalendarItem], Iterable[str]],
    undated_keys: Callable[[UndatedConference], Iterable[str]],
) -> list[MarkdownPage]:
    dated_by_key: dict[str, list[CalendarItem]] = {}
    undated_by_key: dict[str, list[UndatedConference]] = {}

    for item in items:
        for key in item_keys(item):
            dated_by_key.setdefault(key, []).append(item)
    for item in undated_conferences:
        for key in undated_keys(item):
            undated_by_key.setdefault(key, []).append(item)

    pages: list[MarkdownPage] = []
    for key in sorted(set(dated_by_key) | set(undated_by_key)):
        pages.append(
            MarkdownPage(
                site_root / "events" / split / f"{key}.md",
                f"{title_prefix}: {key}",
                tuple(dated_by_key.get(key, ())),
                tuple(undated_by_key.get(key, ())),
            )
        )
    return pages


def render_conference_markdown(
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference] | date = (),
    reference_date: date | None = None,
    title: str = "Events",
) -> str:
    if isinstance(undated_conferences, date):
        reference_date = undated_conferences
        undated_conferences = ()
    today = reference_date or date.today()
    rows = conference_markdown_rows(items)
    upcoming = [row for row in rows if row[0] >= today]
    past = [row for row in rows if row[0] < today]

    lines = [
        f"# {title}",
        "",
        "Tracked events grouped by submission status and timing.",
        "",
        "Always verify important dates and details against the linked official event pages.",
        "",
    ]

    append_submission_opportunities_section(
        lines,
        rows,
        reference_date=today,
    )
    append_timeline_markdown_section(
        lines,
        "Upcoming Events",
        upcoming,
        "No tracked upcoming events.",
        reference_date=today,
    )
    append_undated_conference_section(lines, tuple(undated_conferences))
    append_timeline_markdown_section(
        lines,
        "Past Events",
        past,
        "No tracked past events.",
        reverse_years=True,
        reference_date=today,
    )

    return "\n".join(lines).rstrip() + "\n"


def render_conference_html(
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference] | date = (),
    reference_date: date | None = None,
    title: str = "Events",
) -> str:
    if isinstance(undated_conferences, date):
        reference_date = undated_conferences
        undated_conferences = ()
    today = reference_date or date.today()
    rows = conference_markdown_rows(items)
    upcoming = [row for row in rows if row[0] >= today]
    past = [row for row in rows if row[0] < today]

    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "  <head>",
        '    <meta charset="utf-8">',
        '    <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"    <title>{escape_html(title)}</title>",
        "    <style>",
        "      body { color: #1f2933; font-family: system-ui, -apple-system, BlinkMacSystemFont, "
        '"Segoe UI", sans-serif; line-height: 1.5; margin: 0 auto; max-width: 1180px; padding: 32px 20px; }',
        "      a { color: #0b5cad; }",
        "      table { border-collapse: collapse; margin: 16px 0 28px; width: 100%; }",
        "      th, td { border: 1px solid #d7dde4; padding: 8px 10px; text-align: left; vertical-align: top; }",
        "      th { background: #f3f5f7; }",
        "      .notice { border-left: 4px solid #d97706; background: #fff7ed; padding: 12px 16px; }",
        "      .status-badge { border: 1px solid; border-radius: 6px; display: inline-block; font-size: 0.78rem; "
        "font-weight: 700; line-height: 1; margin: 0 6px 4px 0; padding: 4px 7px; }",
        "      .status-open { background: #dcfce7; border-color: #86efac; color: #166534; }",
        "      .status-closed { background: #f3f4f6; border-color: #d1d5db; color: #4b5563; }",
        "      .status-tbd { background: #fef3c7; border-color: #fcd34d; color: #92400e; }",
        "    </style>",
        "  </head>",
        "  <body>",
        f"    <h1>{escape_html(title)}</h1>",
        "    <p>Tracked events grouped by submission status and timing.</p>",
        '    <p class="notice">Always verify important dates and details against the linked official event pages.</p>',
    ]
    append_submission_opportunities_html_section(lines, rows, reference_date=today)
    append_timeline_html_section(lines, "Upcoming Events", upcoming, "No tracked upcoming events.", today)
    append_undated_conference_html_section(lines, tuple(undated_conferences))
    append_timeline_html_section(
        lines,
        "Past Events",
        past,
        "No tracked past events.",
        today,
        reverse_years=True,
    )
    lines.extend(["  </body>", "</html>"])
    return "\n".join(lines) + "\n"


def has_open_submission_deadline(row: ConferenceMarkdownRow, reference_date: date) -> bool:
    return any(deadline.start >= reference_date for deadline in row[7])


def append_submission_opportunities_section(
    lines: list[str],
    rows: list[ConferenceMarkdownRow],
    *,
    reference_date: date,
) -> None:
    lines.extend(["## Submission Opportunities", ""])
    opportunities = submission_opportunity_rows(rows, reference_date)
    if not opportunities:
        lines.extend(["No tracked events with open submission deadlines.", ""])
        return

    lines.extend(
        [
            "| Deadline | Event | Event Dates | Scope / Co-located Events | Location | Last Checked |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for deadline, row in opportunities:
        start, end_exclusive, title, url, location, last_checked, conferences, _deadlines = row
        lines.append(
            "| "
            f"{markdown_link(submission_deadline_label(deadline, conferences), deadline.url)} | "
            f"{markdown_link(title, url)} | "
            f"{escape_markdown_table(format_date_range(start, end_exclusive))} | "
            f"{conference_scope_cell(title, conferences)} | "
            f"{conference_location_cell(location, conferences)} | "
            f"{format_last_checked(last_checked)} |"
        )
    lines.append("")


def submission_opportunity_rows(
    rows: list[ConferenceMarkdownRow], reference_date: date
) -> list[tuple[CalendarItem, ConferenceMarkdownRow]]:
    opportunities = [(deadline, row) for row in rows for deadline in row[7] if deadline.start >= reference_date]
    return sorted(opportunities, key=lambda value: (value[0].start, value[1][0], value[1][2].lower()))


def append_submission_opportunities_html_section(
    lines: list[str],
    rows: list[ConferenceMarkdownRow],
    *,
    reference_date: date,
) -> None:
    lines.extend(['    <h2 id="submission-opportunities">Submission Opportunities</h2>'])
    opportunities = submission_opportunity_rows(rows, reference_date)
    if not opportunities:
        lines.append("    <p>No tracked events with open submission deadlines.</p>")
        return

    append_html_table_start(
        lines,
        ("Deadline", "Event", "Event Dates", "Scope / Co-located Events", "Location", "Last Checked"),
    )
    for deadline, row in opportunities:
        start, end_exclusive, title, url, location, last_checked, conferences, _deadlines = row
        append_html_table_row(
            lines,
            (
                html_link(submission_deadline_label(deadline, conferences), deadline.url),
                html_link(title, url),
                escape_html(format_date_range(start, end_exclusive)),
                conference_scope_html_cell(title, conferences),
                conference_location_html_cell(location, conferences),
                escape_html(format_last_checked(last_checked)),
            ),
        )
    append_html_table_end(lines)


def append_timeline_markdown_section(
    lines: list[str],
    title: str,
    rows: list[ConferenceMarkdownRow],
    empty_message: str,
    *,
    reverse_years: bool = False,
    reference_date: date,
) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend([empty_message, ""])
        return

    by_year: dict[int, list[ConferenceMarkdownRow]] = {}
    for row in rows:
        by_year.setdefault(row[0].year, []).append(row)

    for year in sorted(by_year, reverse=reverse_years):
        lines.extend(
            [
                f"### {year}",
                "",
                "| Dates | Event | Submission Status | Location | Last Checked |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for start, end_exclusive, title, url, location, last_checked, conferences, deadlines in sorted(
            by_year[year],
            key=lambda value: (value[0], value[2].lower()),
        ):
            lines.append(
                "| "
                f"{escape_markdown_table(format_date_range(start, end_exclusive))} | "
                f"{conference_event_cell(title, url, conferences)} | "
                f"{submission_status_cell(deadlines, conferences, start, reference_date)} | "
                f"{conference_location_cell(location, conferences)} | "
                f"{format_last_checked(last_checked)} |"
            )
        lines.append("")


def append_timeline_html_section(
    lines: list[str],
    title: str,
    rows: list[ConferenceMarkdownRow],
    empty_message: str,
    reference_date: date,
    *,
    reverse_years: bool = False,
) -> None:
    lines.append(f'    <h2 id="{html_id(title)}">{escape_html(title)}</h2>')
    if not rows:
        lines.append(f"    <p>{escape_html(empty_message)}</p>")
        return

    by_year: dict[int, list[ConferenceMarkdownRow]] = {}
    for row in rows:
        by_year.setdefault(row[0].year, []).append(row)

    for year in sorted(by_year, reverse=reverse_years):
        lines.append(f"    <h3>{year}</h3>")
        append_html_table_start(lines, ("Dates", "Event", "Submission Status", "Location", "Last Checked"))
        for start, end_exclusive, title, url, location, last_checked, conferences, deadlines in sorted(
            by_year[year],
            key=lambda value: (value[0], value[2].lower()),
        ):
            append_html_table_row(
                lines,
                (
                    escape_html(format_date_range(start, end_exclusive)),
                    conference_event_html_cell(title, url, conferences),
                    submission_status_html_cell(deadlines, conferences, start, reference_date),
                    conference_location_html_cell(location, conferences),
                    escape_html(format_last_checked(last_checked)),
                ),
            )
        append_html_table_end(lines)


def append_undated_conference_section(lines: list[str], rows: tuple[UndatedConference, ...]) -> None:
    lines.extend(["## Announced / Date TBD", ""])
    if not rows:
        lines.extend(["No tracked announced events without dates.", ""])
        return

    lines.extend(
        [
            "| Event | Known Scope | Location | Source | Last Checked |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(rows, key=lambda item: item.title.lower()):
        lines.append(
            "| "
            f"{markdown_link(row.title, row.url)} | "
            f"{escape_markdown_table(row.scope or 'TBD')} | "
            f"{undated_location_cell(row.location)} | "
            f"{markdown_link('Source', row.source_url or row.url)} | "
            f"{format_last_checked(row.last_checked)} |"
        )
    lines.append("")


def append_undated_conference_html_section(lines: list[str], rows: tuple[UndatedConference, ...]) -> None:
    lines.extend(['    <h2 id="announced-date-tbd">Announced / Date TBD</h2>'])
    if not rows:
        lines.append("    <p>No tracked announced events without dates.</p>")
        return

    append_html_table_start(lines, ("Event", "Known Scope", "Location", "Source", "Last Checked"))
    for row in sorted(rows, key=lambda item: item.title.lower()):
        append_html_table_row(
            lines,
            (
                html_link(row.title, row.url),
                escape_html(row.scope or "TBD"),
                undated_location_html_cell(row.location),
                html_link("Source", row.source_url or row.url),
                escape_html(format_last_checked(row.last_checked)),
            ),
        )
    append_html_table_end(lines)


def append_html_table_start(lines: list[str], headers: tuple[str, ...]) -> None:
    lines.extend(
        [
            "    <table>",
            "      <thead>",
            "        <tr>",
        ]
    )
    for header in headers:
        lines.append(f"          <th>{escape_html(header)}</th>")
    lines.extend(["        </tr>", "      </thead>", "      <tbody>"])


def append_html_table_row(lines: list[str], cells: tuple[str, ...]) -> None:
    lines.append("        <tr>")
    for cell in cells:
        lines.append(f"          <td>{cell}</td>")
    lines.append("        </tr>")


def append_html_table_end(lines: list[str]) -> None:
    lines.extend(["      </tbody>", "    </table>"])


def conference_markdown_rows(
    items: Iterable[CalendarItem],
) -> list[ConferenceMarkdownRow]:
    items_tuple = tuple(items)
    groups: dict[tuple[str, str], list[CalendarItem]] = {}
    for item in items_tuple:
        if item.kind != "conference":
            continue
        if item.co_location_group:
            key = ("group", item.co_location_group)
        else:
            key = ("event", item.uid)
        groups.setdefault(key, []).append(item)

    rows: list[ConferenceMarkdownRow] = []
    for key, group_items in groups.items():
        order = co_location_series_order(group_items) if key[0] == "group" else ()
        conferences = tuple(sorted(group_items, key=lambda item: conference_item_sort_key(item, order)))
        start = min(item.start for item in conferences)
        end_exclusive = max(item.end_exclusive for item in conferences)
        title, url = conference_row_title(key, conferences)
        location = common_value(item.location for item in conferences)
        deadlines = submission_deadlines_for_conferences(items_tuple, conferences)
        last_checked = latest_date(
            item.last_checked for item in (*conferences, *deadlines) if item.last_checked is not None
        )
        rows.append((start, end_exclusive, title, url, location, last_checked, conferences, deadlines))
    return rows


def submission_deadlines_for_conferences(
    items: tuple[CalendarItem, ...],
    conferences: tuple[CalendarItem, ...],
) -> tuple[CalendarItem, ...]:
    return tuple(
        sorted(
            (
                item
                for item in items
                if item.kind.startswith("deadline-")
                and is_submission_deadline(item)
                and deadline_matches_conferences(item, conferences)
            ),
            key=lambda item: (item.start, item.series_slug, item.summary),
        )
    )


def is_submission_deadline(item: CalendarItem) -> bool:
    text = f"{item.kind} {item.summary}".lower()
    return any(keyword in text for keyword in SUBMISSION_DEADLINE_KEYWORDS)


def deadline_matches_conferences(deadline: CalendarItem, conferences: tuple[CalendarItem, ...]) -> bool:
    return any(deadline_matches_conference(deadline, conference) for conference in conferences)


def deadline_matches_conference(deadline: CalendarItem, conference: CalendarItem) -> bool:
    if deadline.series_slug != conference.series_slug:
        return False
    if conference.co_location_group:
        return deadline.co_location_group == conference.co_location_group
    return deadline.uid.startswith(f"{conference.series_slug}-{conference.start.isoformat()}-")


def co_location_series_order(items: Iterable[CalendarItem]) -> tuple[str, ...]:
    return next((item.co_location_series for item in items if item.co_location_series), ())


def conference_item_sort_key(item: CalendarItem, series_order: tuple[str, ...]) -> tuple[int, date, str, str]:
    if item.series_slug in series_order:
        order = series_order.index(item.series_slug)
    else:
        order = len(series_order)
    return order, item.start, item.series, item.summary


def conference_row_title(key: tuple[str, str], conferences: tuple[CalendarItem, ...]) -> tuple[str, str]:
    primary = conferences[0]
    return primary.summary, primary.url


def conference_event_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    event = markdown_link(title, url)
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        contained = ", ".join(markdown_link(conference_short_label(item), item.url) for item in conferences[1:])
        return f"{event} ({contained})"
    return event


def conference_event_html_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    event = html_link(title, url)
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        contained = ", ".join(html_link(conference_short_label(item), item.url) for item in conferences[1:])
        return f"{event} ({contained})"
    return event


def conference_scope_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        return ", ".join(markdown_link(conference_short_label(item), item.url) for item in conferences[1:])
    return escape_markdown_table(event_scope_label(title) or "TBD")


def conference_scope_html_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        return ", ".join(html_link(conference_short_label(item), item.url) for item in conferences[1:])
    return escape_html(event_scope_label(title) or "TBD")


def submission_status_cell(
    deadlines: tuple[CalendarItem, ...],
    conferences: tuple[CalendarItem, ...],
    event_start: date,
    reference_date: date,
) -> str:
    open_deadlines = tuple(deadline for deadline in deadlines if deadline.start >= reference_date)
    if open_deadlines:
        return "Open: " + "<br>".join(
            markdown_link(submission_deadline_label(deadline, conferences), deadline.url) for deadline in open_deadlines
        )
    if deadlines or event_start < reference_date:
        return "Closed"
    return "TBD"


def submission_status_html_cell(
    deadlines: tuple[CalendarItem, ...],
    conferences: tuple[CalendarItem, ...],
    event_start: date,
    reference_date: date,
) -> str:
    open_deadlines = tuple(deadline for deadline in deadlines if deadline.start >= reference_date)
    if open_deadlines:
        return (
            status_badge("Open", "open")
            + "<br>"
            + "<br>".join(
                html_link(submission_deadline_label(deadline, conferences), deadline.url) for deadline in open_deadlines
            )
        )
    if deadlines or event_start < reference_date:
        return status_badge("Closed", "closed")
    return status_badge("TBD", "tbd")


def status_badge(label: str, status: str) -> str:
    return f'<span class="status-badge status-{escape_html(status)}">{escape_html(label)}</span>'


def submission_deadline_label(deadline: CalendarItem, conferences: tuple[CalendarItem, ...]) -> str:
    deadline_name = clean_deadline_name(deadline)
    if len(conferences) > 1:
        prefix = deadline_conference_label(deadline, conferences)
        if prefix:
            deadline_name = f"{prefix}: {deadline_name}"
    return f"{deadline_name}: {deadline.start.isoformat()}"


def format_last_checked(value: date | None) -> str:
    if value is None:
        return "TBD"
    return value.isoformat()


def clean_deadline_name(deadline: CalendarItem) -> str:
    name = deadline.summary
    prefix = f"{deadline.series}: "
    if name.startswith(prefix):
        name = name[len(prefix) :]
    if name.lower().endswith(" deadline"):
        name = name[: -len(" deadline")]
    return name


def deadline_conference_label(deadline: CalendarItem, conferences: tuple[CalendarItem, ...]) -> str:
    for conference in conferences:
        if conference.series_slug == deadline.series_slug:
            return conference_short_label(conference)
    return ""


def conference_location_cell(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    if not location:
        return "TBD"
    maps_url = google_maps_url(location, conferences)
    return "<br>".join(markdown_link(line, maps_url) for line in conference_location_lines(location, conferences))


def conference_location_html_cell(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    if not location:
        return "TBD"
    maps_url = google_maps_url(location, conferences)
    return "<br>".join(html_link(line, maps_url) for line in conference_location_lines(location, conferences))


def undated_location_cell(location: str) -> str:
    if not location:
        return "TBD"
    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(location, safe=',')}"
    return markdown_link(location, maps_url)


def undated_location_html_cell(location: str) -> str:
    if not location:
        return "TBD"
    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(location, safe=',')}"
    return html_link(location, maps_url)


def conference_location_lines(location: str, conferences: tuple[CalendarItem, ...]) -> tuple[str, ...]:
    venue = common_value(item.venue for item in conferences)
    address = common_value(item.address for item in conferences)
    city = common_value(item.city for item in conferences)
    country = common_value(item.country.upper() for item in conferences if item.country)

    if venue and address:
        return venue, address
    if venue:
        city_country = ", ".join(part for part in (city, country) if part)
        if city_country:
            return venue, city_country
        return (venue,)
    if address:
        return (address,)
    if city or country:
        return (", ".join(part for part in (city, country) if part),)
    return (location,)


def google_maps_url(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    query = common_coordinates(conferences) or location
    return f"https://www.google.com/maps/search/?api=1&query={quote(query, safe=',')}"


def common_coordinates(conferences: tuple[CalendarItem, ...]) -> str:
    coordinates = unique_values(
        f"{item.latitude:.7f},{item.longitude:.7f}"
        for item in conferences
        if item.latitude is not None and item.longitude is not None
    )
    if len(coordinates) == 1:
        return coordinates[0]
    return ""


def conference_short_label(item: CalendarItem) -> str:
    parenthetical = re.search(r"\(([^()]*\b\d{4}\b[^()]*)\)\s*$", item.summary)
    if parenthetical:
        return parenthetical.group(1)

    year_suffix = f" {item.start.year}"
    if item.summary.endswith(year_suffix):
        base = item.summary[: -len(year_suffix)]
        for suffix in (" Symposia at Extraction", " at Extraction", " Meeting & Exhibition"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base} {item.start.year}"

    return item.summary


def event_scope_label(title: str) -> str:
    parenthetical = re.search(r"\(([^()]+)\)\s*$", title)
    if not parenthetical:
        return ""
    return parenthetical.group(1)


def write_rss_feed(site_root: Path, items: Iterable[CalendarItem], updated_at: datetime) -> None:
    (site_root / "events.xml").write_text(render_rss_feed(items, updated_at), encoding="utf-8", newline="\n")


def render_rss_feed(items: Iterable[CalendarItem], updated_at: datetime) -> str:
    rss_date = format_datetime(normalize_datetime(updated_at), usegmt=True)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        "    <title>Event Updates</title>",
        f"    <link>{escape_xml(SITE_URL)}</link>",
        f'    <atom:link href="{escape_xml(SITE_URL)}events.xml" rel="self" type="application/rss+xml" />',
        f"    <description>{escape_xml(DISCLAIMER)}</description>",
        "    <language>en</language>",
        f"    <pubDate>{rss_date}</pubDate>",
        f"    <lastBuildDate>{rss_date}</lastBuildDate>",
        "    <ttl>360</ttl>",
    ]

    for item in items:
        lines.extend(render_rss_item(item))

    lines.extend(["  </channel>", "</rss>"])
    return "\n".join(lines) + "\n"


def render_rss_item(item: CalendarItem) -> list[str]:
    tags = unique_values(
        tag
        for tag in (
            "conference",
            item.kind,
            item.series_slug,
            item.country,
            *item.event_types,
            *item.categories,
            *item.topics,
        )
        if tag
    )
    lines = [
        "    <item>",
        f"      <title>{escape_xml(item.summary)}</title>",
        f"      <link>{escape_xml(item.url or SITE_URL)}</link>",
        f'      <guid isPermaLink="false">{escape_xml(item.uid)}</guid>',
        f"      <description>{escape_xml(rss_item_description(item))}</description>",
    ]
    for tag in tags:
        lines.append(f"      <category>{escape_xml(tag)}</category>")
    lines.append("    </item>")
    return lines


def rss_item_description(item: CalendarItem) -> str:
    return build_description(
        [
            ("Date", format_item_date_range(item)),
            ("Status", item.status.title()),
            ("Location", item.location),
            ("Details", item.description),
        ]
    )


def format_item_date_range(item: CalendarItem) -> str:
    return format_date_range(item.start, item.end_exclusive)


def format_date_range(start: date, end_exclusive: date) -> str:
    end_inclusive = end_exclusive - timedelta(days=1)
    if start == end_inclusive:
        return start.isoformat()
    return f"{start.isoformat()} to {end_inclusive.isoformat()}"


def first_value(values: Iterable[str]) -> str:
    return next((value for value in values if value), "")


def undated_location(event: dict) -> str:
    country = optional_str(event, "country").upper()
    return ", ".join(part for part in (optional_str(event, "venue"), optional_str(event, "city"), country) if part)


def common_value(values: Iterable[str]) -> str:
    unique = unique_values(value for value in values if value)
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return ""
    return "; ".join(unique)


def write_site_index(output_dir: Path, feeds: list[Feed]) -> None:
    site_root = output_dir.parent
    links = "\n".join(
        f'        <li><a href="{escape_html(feed.path.relative_to(site_root).as_posix())}">'
        f"{escape_html(feed.name)}</a> ({len(feed.items)} items)</li>"
        for feed in feeds
    )
    conference_links = "\n".join(
        f'        <li><a href="{escape_html(path)}">{escape_html(name)}</a></li>'
        for name, path in conference_markdown_links(output_dir, feeds)
    )
    page = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Industrial Events</title>
    <link rel="alternate" type="application/rss+xml" title="Event Updates RSS" href="events.xml">
    <style>
      body {{
        color: #1f2933;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        line-height: 1.5;
        margin: 0 auto;
        max-width: 920px;
        padding: 32px 20px;
      }}
      a {{ color: #0b5cad; }}
      code {{
        background: #f3f5f7;
        border-radius: 4px;
        padding: 2px 5px;
      }}
      li {{ margin: 6px 0; }}
      .notice {{
        border-left: 4px solid #d97706;
        background: #fff7ed;
        padding: 12px 16px;
      }}
    </style>
  </head>
  <body>
    <h1>Industrial Events</h1>
    <p>Subscribe to generated iCalendar feeds for tracked events and deadlines.</p>
    <p>Primary feed: <a href="calendars/all.ics"><code>calendars/all.ics</code></a></p>
    <p>Event list: <a href="events/all.html"><code>events/all.html</code></a></p>
    <p>RSS event stream: <a href="events.xml"><code>events.xml</code></a></p>
    <p>GitHub repository: <a href="{REPOSITORY_URL}">{REPOSITORY_URL}</a></p>
    <h2>Feeds</h2>
    <ul>
{links}
    </ul>
    <h2>Event Lists</h2>
    <ul>
{conference_links}
    </ul>
    <h2>Disclaimer</h2>
    <p class="notice">{escape_html(DISCLAIMER)}</p>
  </body>
</html>
"""
    (site_root / "index.html").write_text(page, encoding="utf-8", newline="\n")


def conference_markdown_links(output_dir: Path, feeds: list[Feed]) -> list[tuple[str, str]]:
    links = [("All Events", "events/all.html")]
    for feed in feeds:
        relative = feed.path.relative_to(output_dir)
        if not relative.parts or relative.parts[0] not in {"series", "category", "event-type", "domain", "group"}:
            continue
        html_path = Path("events", relative).with_suffix(".html").as_posix()
        links.append((feed.name, html_path))
    return links


def clean_stale_feeds(output_dir: Path, expected_paths: set[Path]) -> int:
    if not output_dir.exists():
        return 0
    cleaned = 0
    for path in output_dir.rglob("*.ics"):
        if path.resolve() in expected_paths:
            continue
        LOGGER.debug("Removing stale iCalendar feed: %s", path)
        try:
            path.unlink()
        except PermissionError:
            path.write_text(render_calendar("Removed Calendar Feed", ()), encoding="utf-8", newline="\n")
        cleaned += 1
    return cleaned


def clean_stale_conference_docs(site_root: Path, expected_paths: set[Path]) -> int:
    markdown_root = site_root / "events"
    if not markdown_root.exists():
        return 0

    cleaned = 0
    for pattern in ("*.md", "*.html"):
        for path in markdown_root.rglob(pattern):
            if path.resolve() in expected_paths:
                continue
            LOGGER.debug("Removing stale event doc file: %s", path)
            path.unlink()
            cleaned += 1
    return cleaned


def remove_legacy_conference_markdown(site_root: Path) -> None:
    legacy_path = site_root / "conferences.md"
    if not legacy_path.exists():
        return
    LOGGER.debug("Removing legacy conference Markdown file: %s", legacy_path)
    legacy_path.unlink()


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


def format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def escape_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("\r\n", "\\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    )


def escape_xml(value: str) -> str:
    return escape_html(value)


def markdown_link(label: str, url: str) -> str:
    escaped_label = escape_markdown_table(label).replace("[", "\\[").replace("]", "\\]")
    if not url:
        return escaped_label
    escaped_url = url.replace("<", "%3C").replace(">", "%3E")
    return f"[{escaped_label}](<{escaped_url}>)"


def html_link(label: str, url: str) -> str:
    escaped_label = escape_html(label)
    if not url:
        return escaped_label
    return f'<a href="{escape_html(url)}">{escaped_label}</a>'


def html_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def escape_markdown_table(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", " ").replace("\n", " ").replace("|", "\\|")


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def fold_line(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    chunks: list[str] = []
    remaining = line
    limit = 75
    while len(remaining.encode("utf-8")) > limit:
        cut = 0
        byte_count = 0
        for index, char in enumerate(remaining):
            char_len = len(char.encode("utf-8"))
            if byte_count + char_len > limit:
                break
            cut = index + 1
            byte_count += char_len
        chunks.append(remaining[:cut])
        remaining = " " + remaining[cut:]
        limit = 75
    chunks.append(remaining)
    return "\r\n".join(chunks)


if __name__ == "__main__":
    raise SystemExit(main())
