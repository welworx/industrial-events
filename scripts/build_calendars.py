from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
DEFAULT_SOURCE_DIR = ROOT / "conferences"
DEFAULT_OUTPUT_DIR = ROOT / "public" / "calendars"
PRODID = "-//Conference Calendars//EN"
DTSTAMP = "20260504T000000Z"
SITE_URL = "https://welworx.github.io/conferences/"
REPOSITORY_URL = "https://github.com/welworx/conferences"
RSS_UPDATED_ENV = "CONFERENCE_FEED_UPDATED"
DEFAULT_FEED_UPDATED = datetime(2026, 5, 4, tzinfo=UTC)
README_SUBMISSION_START = "<!-- generated:submission-opportunities:start -->"
README_SUBMISSION_END = "<!-- generated:submission-opportunities:end -->"
DISCLAIMER = (
    "This calendar makes existing public conference information easier to access. "
    "It may be incomplete, outdated, or wrong. Always verify important dates and details "
    "against the official conference source. The maintainer is not responsible for missing "
    "updates, incorrect information, missed deadlines, travel costs, registration decisions, "
    "or any other consequence of using this feed."
)
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOP_LEVEL_KEYS = {"series", "slug", "website", "categories", "topics", "sources", "events"}
EVENT_KEYS = {
    "name",
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
STATUS_MAP = {
    "confirmed": "CONFIRMED",
    "tentative": "TENTATIVE",
    "estimated": "TENTATIVE",
    "cancelled": "CANCELLED",
}
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
    parser = argparse.ArgumentParser(description="Build iCalendar feeds from conference YAML files.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        feeds = build_calendars(args.source, args.output)
    except CalendarBuildError as exc:
        LOGGER.error("Build failed: %s", exc)
        return 1

    LOGGER.info("Generated %d calendar feed(s) in %s", len(feeds), args.output)
    return 0


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(levelname)s %(message)s", force=True)


def build_calendars(
    source_dir: Path,
    output_dir: Path,
    updated_at: datetime | None = None,
    reference_date: date | None = None,
) -> list[Feed]:
    LOGGER.info("Building conference outputs")
    LOGGER.info("Source directory: %s", source_dir)
    LOGGER.info("Output directory: %s", output_dir)
    updated_at = feed_updated_at() if updated_at is None else normalize_datetime(updated_at)
    LOGGER.info("RSS build timestamp: %s", updated_at.isoformat())
    items = load_items(source_dir)
    conference_count = sum(1 for item in items if item.kind == "conference")
    deadline_count = len(items) - conference_count
    LOGGER.info(
        "Loaded %d calendar item(s): %d conference(s), %d deadline(s)",
        len(items),
        conference_count,
        deadline_count,
    )
    feeds = build_feeds(items, output_dir)
    LOGGER.info("Built %d iCalendar feed definition(s)", len(feeds))
    undated_conferences = load_undated_conferences(source_dir)
    LOGGER.info("Loaded %d announced conference(s) without calendar dates", len(undated_conferences))
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
    LOGGER.info("Writing conference Markdown list")
    write_conference_markdown(output_dir.parent, items, undated_conferences, reference_date)
    LOGGER.info("Updating README submission opportunities")
    write_readme_submission_opportunities(output_dir.parent.parent / "README.md", items, reference_date)
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
    paths = sorted(source_files(source_dir))
    LOGGER.info("Found %d conference source file(s)", len(paths))

    for path in paths:
        LOGGER.debug("Loading conference source: %s", path)
        relative_parts = path.relative_to(source_dir).parts
        if len(relative_parts) < 2:
            raise CalendarBuildError(f"{path}: conference YAML files must be under conferences/<domain>/")
        domain = relative_parts[0]
        if not SLUG_RE.fullmatch(domain):
            raise CalendarBuildError(f"{path}: domain folder must be a lowercase slug")
        data = load_yaml(path)
        validate_unknown_keys(path, data, TOP_LEVEL_KEYS)

        series = require_str(path, data, "series")
        slug = require_slug(path, data, "slug")
        if slug in seen_slugs:
            raise CalendarBuildError(f"{path}: duplicate slug {slug!r}; first used in {seen_slugs[slug]}")
        seen_slugs[slug] = path

        categories = require_slug_list(path, data, "categories")
        topics = optional_slug_list(path, data, "topics")
        website = optional_str(data, "website")
        series_sources = source_urls(path, data, "top level")
        series_checked_dates = source_checked_dates(path, data, "top level")
        events = data.get("events", [])
        if not isinstance(events, list):
            raise CalendarBuildError(f"{path}: events must be a list of YAML objects")

        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise CalendarBuildError(f"{path}: event {index} must be a YAML object")
            validate_unknown_keys(path, event, EVENT_KEYS, f"event {index}")
            items.extend(
                items_for_event(
                    path,
                    domain,
                    series,
                    slug,
                    categories,
                    topics,
                    website,
                    series_sources,
                    series_checked_dates,
                    index,
                    event,
                )
            )

    return sorted(items, key=lambda item: (item.start, item.series_slug, item.kind, item.summary))


def load_undated_conferences(source_dir: Path) -> list[UndatedConference]:
    if not source_dir.exists():
        return []

    undated: list[UndatedConference] = []
    for path in sorted(source_files(source_dir)):
        relative_parts = path.relative_to(source_dir).parts
        if len(relative_parts) < 2:
            raise CalendarBuildError(f"{path}: conference YAML files must be under conferences/<domain>/")
        domain = relative_parts[0]
        if not SLUG_RE.fullmatch(domain):
            raise CalendarBuildError(f"{path}: domain folder must be a lowercase slug")
        data = load_yaml(path)
        validate_unknown_keys(path, data, TOP_LEVEL_KEYS)
        slug = require_slug(path, data, "slug")
        categories = require_slug_list(path, data, "categories")
        website = optional_str(data, "website")
        series_checked_dates = source_checked_dates(path, data, "top level")
        series_sources = source_urls(path, data, "top level")
        events = data.get("events", [])
        if not isinstance(events, list):
            raise CalendarBuildError(f"{path}: events must be a list of YAML objects")

        for event_index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise CalendarBuildError(f"{path}: event {event_index} must be a YAML object")
            validate_unknown_keys(path, event, EVENT_KEYS, f"event {event_index}")
            start = optional_date(path, event, "start", f"event {event_index}")
            end = optional_date(path, event, "end", f"event {event_index}")
            if start is not None or end is not None:
                continue

            title = require_str(path, event, "name", f"event {event_index}")
            co_location = optional_co_location(path, event, f"event {event_index}")
            event_sources = source_urls(path, event, f"event {event_index}")
            checked_dates = (*series_checked_dates, *source_checked_dates(path, event, f"event {event_index}"))
            source_url = first_value((*event_sources, *series_sources))
            undated.append(
                UndatedConference(
                    series_slug=slug,
                    domain=domain,
                    categories=categories,
                    title=title,
                    url=optional_str(event, "url") or website,
                    scope=event_scope_label(title),
                    location=undated_location(event),
                    source_url=source_url,
                    last_checked=latest_date(checked_dates),
                    co_location_group=co_location.group,
                )
            )

    return sorted(undated, key=lambda item: (item.title.lower(), item.url))


def items_for_event(
    path: Path,
    domain: str,
    series: str,
    slug: str,
    categories: tuple[str, ...],
    topics: tuple[str, ...],
    website: str,
    series_sources: tuple[str, ...],
    series_checked_dates: tuple[date, ...],
    event_index: int,
    event: dict,
) -> list[CalendarItem]:
    event_name = require_str(path, event, "name", f"event {event_index}")
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
    url = optional_str(event, "url") or website
    status = normalize_status(path, optional_str(event, "status") or "confirmed", f"event {event_index}")
    event_sources = unique_values((*series_sources, *source_urls(path, event, f"event {event_index}")))
    event_checked_dates = (*series_checked_dates, *source_checked_dates(path, event, f"event {event_index}"))
    event_last_checked = latest_date(event_checked_dates)
    description = build_description(
        [
            ("Series", series),
            ("Kind", "conference"),
            ("Categories", ", ".join(categories)),
            ("Topics", ", ".join(topics)),
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
        uid=f"{slug}-{start.isoformat()}-event@conference-calendars",
        summary=event_name,
        start=start,
        end_exclusive=end + timedelta(days=1),
        series=series,
        series_slug=slug,
        domain=domain,
        categories=categories,
        topics=topics,
        country=country,
        kind="conference",
        status=status,
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
        deadline_summary = f"{series}: {deadline_name}"
        deadline_description = build_description(
            [
                ("Series", series),
                ("Event", event_name),
                ("Kind", f"{deadline_type} deadline"),
                ("Categories", ", ".join(categories)),
                ("Topics", ", ".join(topics)),
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
                uid=f"{slug}-{start.isoformat()}-{deadline_type}-{deadline_date.isoformat()}@conference-calendars",
                summary=deadline_summary,
                start=deadline_date,
                end_exclusive=deadline_date + timedelta(days=1),
                series=series,
                series_slug=slug,
                domain=domain,
                categories=categories,
                topics=topics,
                country=country,
                kind=f"deadline-{deadline_type}",
                status=deadline_status,
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
    feeds = [Feed(output_dir / "all.ics", "All Conferences", items_tuple)]

    by_series: dict[str, list[CalendarItem]] = {}
    by_category: dict[str, list[CalendarItem]] = {}
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

    for slug, feed_items in sorted(by_series.items()):
        feeds.append(Feed(output_dir / "series" / f"{slug}.ics", f"Conference Series: {slug}", tuple(feed_items)))
    for category, feed_items in sorted(by_category.items()):
        feeds.append(
            Feed(output_dir / "category" / f"{category}.ics", f"Conference Category: {category}", tuple(feed_items))
        )
    for country, feed_items in sorted(by_country.items()):
        feeds.append(
            Feed(
                output_dir / "country" / f"{country}.ics",
                f"Conference Country: {country.upper()}",
                tuple(feed_items),
            )
        )
    for domain, feed_items in sorted(by_domain.items()):
        feeds.append(Feed(output_dir / "domain" / f"{domain}.ics", f"Conference Domain: {domain}", tuple(feed_items)))
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
        tag for tag in ("conference", item.kind, item.series_slug, item.country, *item.categories, *item.topics) if tag
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
        f"X-CONFERENCE-SERIES:{escape_text(item.series)}",
        f"X-CONFERENCE-SERIES-SLUG:{escape_text(item.series_slug)}",
        f"X-CONFERENCE-DOMAIN:{escape_text(item.domain)}",
    ]
    if item.country:
        lines.append(f"X-CONFERENCE-COUNTRY:{escape_text(item.country.upper())}")
    if item.location:
        lines.append(f"LOCATION:{escape_text(item.location)}")
    if item.latitude is not None and item.longitude is not None:
        lines.append(f"GEO:{item.latitude:.7f};{item.longitude:.7f}")
    if item.co_location_group:
        lines.append(f"X-CONFERENCE-COLOCATED-GROUP:{escape_text(item.co_location_group)}")
    if item.co_location_series:
        lines.append(f"X-CONFERENCE-COLOCATED-SERIES:{','.join(escape_text(slug) for slug in item.co_location_series)}")
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
    LOGGER.info("Cleaned %d stale conference doc file(s)", cleaned)
    LOGGER.info("Writing %d conference doc page(s)", len(pages))
    for page in pages:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing conference Markdown file: %s", page.path)
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
        LOGGER.debug("Writing conference HTML file: %s", html_path)
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


def write_readme_submission_opportunities(
    readme_path: Path,
    items: Iterable[CalendarItem],
    reference_date: date | None = None,
) -> None:
    if not readme_path.exists():
        LOGGER.debug("Skipping README update; file does not exist: %s", readme_path)
        return

    current = readme_path.read_text(encoding="utf-8")
    section = render_readme_submission_opportunities(items, reference_date)
    updated = replace_readme_submission_section(current, section)
    if updated == current:
        return
    readme_path.write_text(updated, encoding="utf-8", newline="\n")


def render_readme_submission_opportunities(
    items: Iterable[CalendarItem],
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    opportunities = submission_opportunity_rows(conference_markdown_rows(items), today)
    lines = [
        "## Current Submission Opportunities",
        "",
        README_SUBMISSION_START,
        "",
        "This section is generated by `uv run python scripts/build_calendars.py`.",
        "",
        "Full list: "
        "[https://welworx.github.io/conferences/conferences/all.html#submission-opportunities]"
        "(https://welworx.github.io/conferences/conferences/all.html#submission-opportunities).",
        "",
    ]
    if not opportunities:
        lines.append("No tracked conferences with open submission deadlines.")
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
                f"{escape_markdown_table(format_date_range(start, end_exclusive))} | "
                f"{conference_scope_cell(title, conferences)} | "
                f"{format_last_checked(last_checked)} |"
            )
    lines.extend(["", README_SUBMISSION_END])
    return "\n".join(lines) + "\n"


def replace_readme_submission_section(content: str, section: str) -> str:
    heading = "## Current Submission Opportunities\n"
    heading_start = content.find(heading)
    marker_start = content.find(README_SUBMISSION_START, heading_start if heading_start != -1 else 0)
    marker_end = content.find(README_SUBMISSION_END, marker_start if marker_start != -1 else 0)
    if heading_start != -1 and marker_start != -1 and marker_end != -1:
        end = marker_end + len(README_SUBMISSION_END)
        while end < len(content) and content[end] in "\r\n":
            end += 1
        return content[:heading_start] + section + "\n" + content[end:]

    insert_marker = "\n## Included Conferences"
    insert_at = content.find(insert_marker)
    if insert_at == -1:
        return content.rstrip() + "\n\n" + section
    return content[: insert_at + 1] + section + "\n" + content[insert_at + 1 :]


def conference_markdown_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference],
) -> tuple[MarkdownPage, ...]:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_conferences)
    pages = [MarkdownPage(site_root / "conferences" / "all.md", "Conferences", items_tuple, undated_tuple)]

    pages.extend(
        markdown_split_pages(
            site_root,
            "series",
            "Conference Series",
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
            "Conference Category",
            items_tuple,
            undated_tuple,
            item_keys=lambda item: item.categories,
            undated_keys=lambda item: item.categories,
        )
    )
    pages.extend(
        markdown_split_pages(
            site_root,
            "domain",
            "Conference Domain",
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
                site_root / "conferences" / split / f"{key}.md",
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
    title: str = "Conferences",
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
        "Tracked conference events grouped by submission status and timing.",
        "",
        "Always verify important dates and details against the linked official conference pages.",
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
        "No tracked upcoming conferences.",
        reference_date=today,
    )
    append_undated_conference_section(lines, tuple(undated_conferences))
    append_timeline_markdown_section(
        lines,
        "Past Events",
        past,
        "No tracked past conferences.",
        reverse_years=True,
        reference_date=today,
    )

    return "\n".join(lines).rstrip() + "\n"


def render_conference_html(
    items: Iterable[CalendarItem],
    undated_conferences: Iterable[UndatedConference] | date = (),
    reference_date: date | None = None,
    title: str = "Conferences",
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
        "    </style>",
        "  </head>",
        "  <body>",
        f"    <h1>{escape_html(title)}</h1>",
        "    <p>Tracked conference events grouped by submission status and timing.</p>",
        '    <p class="notice">Always verify important dates and details against the linked official '
        "conference pages.</p>",
    ]
    append_submission_opportunities_html_section(lines, rows, reference_date=today)
    append_timeline_html_section(lines, "Upcoming Events", upcoming, "No tracked upcoming conferences.", today)
    append_undated_conference_html_section(lines, tuple(undated_conferences))
    append_timeline_html_section(
        lines,
        "Past Events",
        past,
        "No tracked past conferences.",
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
        lines.extend(["No tracked conferences with open submission deadlines.", ""])
        return

    lines.extend(
        [
            "| Deadline | Event | Event Dates | Scope / Co-located Conferences | Location | Last Checked |",
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
        lines.append("    <p>No tracked conferences with open submission deadlines.</p>")
        return

    append_html_table_start(
        lines,
        ("Deadline", "Event", "Event Dates", "Scope / Co-located Conferences", "Location", "Last Checked"),
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
        lines.extend(["No tracked announced conferences without dates.", ""])
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
        lines.append("    <p>No tracked announced conferences without dates.</p>")
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
        return "Open: " + "<br>".join(
            html_link(submission_deadline_label(deadline, conferences), deadline.url) for deadline in open_deadlines
        )
    if deadlines or event_start < reference_date:
        return "Closed"
    return "TBD"


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
        "    <title>Conference Events</title>",
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
        tag for tag in ("conference", item.kind, item.series_slug, item.country, *item.categories, *item.topics) if tag
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
    <title>Conference Calendars</title>
    <link rel="alternate" type="application/rss+xml" title="Conference Events RSS" href="events.xml">
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
    <h1>Conference Calendars</h1>
    <p>Subscribe to generated iCalendar feeds for tracked conference events and deadlines.</p>
    <p>Primary feed: <a href="calendars/all.ics"><code>calendars/all.ics</code></a></p>
    <p>Conference list: <a href="conferences/all.html"><code>conferences/all.html</code></a></p>
    <p>RSS event stream: <a href="events.xml"><code>events.xml</code></a></p>
    <p>GitHub repository: <a href="{REPOSITORY_URL}">{REPOSITORY_URL}</a></p>
    <h2>Feeds</h2>
    <ul>
{links}
    </ul>
    <h2>Conference Lists</h2>
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
    links = [("All Conferences", "conferences/all.html")]
    for feed in feeds:
        relative = feed.path.relative_to(output_dir)
        if not relative.parts or relative.parts[0] not in {"series", "category", "domain", "group"}:
            continue
        html_path = Path("conferences", relative).with_suffix(".html").as_posix()
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
    markdown_root = site_root / "conferences"
    if not markdown_root.exists():
        return 0

    cleaned = 0
    for pattern in ("*.md", "*.html"):
        for path in markdown_root.rglob(pattern):
            if path.resolve() in expected_paths:
                continue
            LOGGER.debug("Removing stale conference doc file: %s", path)
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


def require_slug_list(path: Path, data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise CalendarBuildError(f"{path}: top level field {key!r} must be a non-empty list")
    slugs = tuple(item for item in value if isinstance(item, str) and SLUG_RE.fullmatch(item))
    if len(slugs) != len(value):
        raise CalendarBuildError(f"{path}: top level field {key!r} must contain only lowercase slugs")
    return slugs


def optional_slug_list(path: Path, data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise CalendarBuildError(f"{path}: top level field {key!r} must be a list")
    slugs = tuple(item for item in value if isinstance(item, str) and SLUG_RE.fullmatch(item))
    if len(slugs) != len(value):
        raise CalendarBuildError(f"{path}: top level field {key!r} must contain only lowercase slugs")
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
