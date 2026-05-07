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

from industrial_events import data
from industrial_events.config import (
    DEFAULT_CONFIG_PATH,
    BuildConfig,
    ConfigError,
    config_with_overrides,
    load_build_config,
)

EVENT_KEYS = data.EVENT_KEYS
SLUG_RE = data.SLUG_RE
SOURCE_PAGE_KEYS = data.SOURCE_PAGE_KEYS
STATUS_MAP = data.STATUS_MAP
SUBMISSION_DEADLINE_KEYWORDS = data.SUBMISSION_DEADLINE_KEYWORDS
CalendarBuildError = data.CalendarBuildError
CalendarItem = data.CalendarItem
CoLocation = data.CoLocation
SeriesMetadata = data.SeriesMetadata
UndatedEvent = data.UndatedEvent
build_description = data.build_description
common_value = data.common_value
deadline_history = data.deadline_history
event_files = data.event_files
first_value = data.first_value
latest_date = data.latest_date
load_event_file = data.load_event_file
load_items = data.load_items
load_series_metadata = data.load_series_metadata
load_series_metadata_file = data.load_series_metadata_file
load_undated_events = data.load_undated_events
load_yaml = data.load_yaml
normalize_status = data.normalize_status
optional_date = data.optional_date
optional_slug_list = data.optional_slug_list
optional_str = data.optional_str
require_slug = data.require_slug
require_slug_list = data.require_slug_list
require_str = data.require_str
series_metadata_files = data.series_metadata_files
source_checked_dates = data.source_checked_dates
source_files = data.source_files
source_urls = data.source_urls
unique_values = data.unique_values
validate_unknown_keys = data.validate_unknown_keys

ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("build_site")
README_UPCOMING_START = "<!-- generated:upcoming-events:start -->"
README_UPCOMING_END = "<!-- generated:upcoming-events:end -->"
README_SUBMISSION_START = "<!-- generated:submission-opportunities:start -->"
README_SUBMISSION_END = "<!-- generated:submission-opportunities:end -->"
README_SERIES_START = "<!-- generated:series-overview:start -->"
README_SERIES_END = "<!-- generated:series-overview:end -->"
README_SOURCES_START = "<!-- generated:overview-sources:start -->"
README_SOURCES_END = "<!-- generated:overview-sources:end -->"


@dataclass(frozen=True)
class Feed:
    path: Path
    name: str
    items: tuple[CalendarItem, ...]


@dataclass(frozen=True)
class MarkdownPage:
    path: Path
    title: str
    items: tuple[CalendarItem, ...]
    undated_events: tuple[UndatedEvent, ...] = ()


EventMarkdownRow = tuple[
    date,
    date,
    str,
    str,
    str,
    date | None,
    tuple[CalendarItem, ...],
    tuple[CalendarItem, ...],
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GitHub Pages outputs from event YAML files.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = config_with_overrides(
            load_build_config(args.config),
            source_dir=args.source,
            output_dir=args.output,
            readme_path=args.readme,
            sources_dir=args.sources,
        )
        feeds = build_site(config)
    except (CalendarBuildError, ConfigError) as exc:
        LOGGER.error("Build failed: %s", exc)
        return 1

    LOGGER.info("Generated %d calendar feed(s) in %s", len(feeds), config.output_dir)
    return 0


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(levelname)s %(message)s", force=True)


def build_site(
    config: BuildConfig,
    updated_at: datetime | None = None,
    reference_date: date | None = None,
) -> list[Feed]:
    LOGGER.info("Building event outputs")
    LOGGER.info("Source directory: %s", config.source_dir)
    LOGGER.info("Output directory: %s", config.output_dir)
    if not config.source_dir.exists():
        raise CalendarBuildError(f"source directory does not exist: {config.source_dir}")

    updated_at = feed_updated_at(config) if updated_at is None else normalize_datetime(updated_at)
    LOGGER.info("RSS build timestamp: %s", updated_at.isoformat())
    items = load_items(config.source_dir, config)
    event_count = sum(1 for item in items if item.kind == "event")
    deadline_count = len(items) - event_count
    LOGGER.info(
        "Loaded %d calendar item(s): %d event(s), %d deadline(s)",
        len(items),
        event_count,
        deadline_count,
    )
    feeds = build_feeds(items, config.output_dir)
    LOGGER.info("Built %d iCalendar feed definition(s)", len(feeds))
    undated_events = load_undated_events(config.source_dir)
    LOGGER.info("Loaded %d announced event(s) without calendar dates", len(undated_events))
    stale_count = clean_stale_feeds(config.output_dir, {feed.path.resolve() for feed in feeds}, config, updated_at)
    LOGGER.info("Cleaned %d stale iCalendar feed(s)", stale_count)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Writing %d iCalendar feed file(s)", len(feeds))
    for feed in feeds:
        feed.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing iCalendar feed: %s (%d item(s))", feed.path, len(feed.items))
        feed.path.write_text(render_calendar(feed.name, feed.items, config, updated_at), encoding="utf-8", newline="\n")

    LOGGER.info("Writing calendar feed index")
    write_index(config.output_dir, feeds)
    LOGGER.info("Writing event list pages")
    write_event_pages(config.output_dir.parent, items, undated_events, reference_date)
    LOGGER.info("Updating README overview sections")
    write_readme_overview(
        config.readme_path,
        config.source_dir,
        config.sources_dir,
        items,
        undated_events,
        config,
        reference_date,
    )
    LOGGER.info("Writing RSS event stream")
    write_rss_feed(config.output_dir.parent, items, updated_at, config)
    LOGGER.info("Writing site index page")
    write_site_index(config.output_dir, feeds, config)
    return feeds


def feed_updated_at(config: BuildConfig) -> datetime:
    env_value = os.environ.get(config.rss_updated_env, "").strip()
    if env_value:
        return parse_datetime(env_value, config.rss_updated_env)

    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cI"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return config.default_feed_updated

    value = completed.stdout.strip()
    if not value:
        return config.default_feed_updated
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


def render_calendar(name: str, items: Iterable[CalendarItem], config: BuildConfig, updated_at: datetime) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{config.product_id}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:UTC",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
        f"X-WR-CALNAME:{escape_text(name)}",
        f"X-WR-CALDESC:{escape_text(config.disclaimer)}",
    ]

    for item in items:
        lines.extend(render_event(item, format_datetime_ics(updated_at)))

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def render_event(item: CalendarItem, timestamp: str) -> list[str]:
    tags = unique_values(
        tag
        for tag in (
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
        f"DTSTAMP:{timestamp}",
        f"CREATED:{timestamp}",
        f"LAST-MODIFIED:{timestamp}",
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


def write_event_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent] = (),
    reference_date: date | None = None,
) -> None:
    pages = event_markdown_pages(site_root, items, undated_events)
    remove_legacy_conference_markdown(site_root)
    expected_paths = {page.path.resolve() for page in pages} | {
        page.path.with_suffix(".html").resolve() for page in pages
    }
    cleaned = clean_stale_event_docs(site_root, expected_paths)
    LOGGER.info("Cleaned %d stale event doc file(s)", cleaned)
    LOGGER.info("Writing %d event doc page(s)", len(pages))
    for page in pages:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing event Markdown file: %s", page.path)
        page.path.write_text(
            render_event_markdown(
                page.items,
                page.undated_events,
                reference_date,
                title=page.title,
            ),
            encoding="utf-8",
            newline="\n",
        )
        html_path = page.path.with_suffix(".html")
        LOGGER.debug("Writing event HTML file: %s", html_path)
        html_path.write_text(
            render_event_html(
                page.items,
                page.undated_events,
                reference_date,
                title=page.title,
            ),
            encoding="utf-8",
            newline="\n",
        )


def write_readme_overview(
    readme_path: Path,
    source_dir: Path,
    sources_dir: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None = None,
) -> None:
    if not readme_path.exists():
        LOGGER.debug("Skipping README update; file does not exist: %s", readme_path)
        return

    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    current = readme_path.read_text(encoding="utf-8")
    updated = replace_marked_section(
        current,
        README_UPCOMING_START,
        README_UPCOMING_END,
        render_readme_upcoming_events(items_tuple, config, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SUBMISSION_START,
        README_SUBMISSION_END,
        render_readme_submission_opportunities(items_tuple, config, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SERIES_START,
        README_SERIES_END,
        render_readme_series_overview(
            load_series_metadata(source_dir),
            items_tuple,
            undated_tuple,
            config,
            reference_date,
        ),
    )
    updated = replace_marked_section(
        updated,
        README_SOURCES_START,
        README_SOURCES_END,
        render_readme_overview_sources(sources_dir),
    )
    if updated == current:
        return
    readme_path.write_text(updated, encoding="utf-8", newline="\n")


def render_readme_upcoming_events(
    items: Iterable[CalendarItem],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    rows = [row for row in event_markdown_rows(items) if row[0] >= today]
    lines = [
        README_UPCOMING_START,
        "",
        f"Full list: [All upcoming events]({config.site_url}events/all.html#upcoming-events).",
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
                f"- {compact_date_range(start, end_exclusive)}: {event_cell(title, url, conferences)} {event_details}"
            )
    lines.extend(["", README_UPCOMING_END])
    return "\n".join(lines)


def render_readme_submission_opportunities(
    items: Iterable[CalendarItem],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    opportunities = submission_opportunity_rows(event_markdown_rows(items), today)
    lines = [
        README_SUBMISSION_START,
        "",
        f"Full list: [All submission opportunities]({config.site_url}events/all.html#submission-opportunities).",
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
                f"{event_scope_cell(title, conferences)} | "
                f"{format_last_checked(last_checked)} |"
            )
    lines.extend(["", README_SUBMISSION_END])
    return "\n".join(lines)


def render_readme_series_overview(
    metadata: Iterable[SeriesMetadata],
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    lines = [
        README_SERIES_START,
        "",
    ]
    for series in sorted(metadata, key=lambda value: value.series.lower()):
        series_items = tuple(item for item in items_tuple if item.series_slug == series.slug)
        series_undated = tuple(item for item in undated_tuple if item.series_slug == series.slug)
        series_link = markdown_link(series.series, series_page_url(series.slug, config))
        lines.extend(
            [
                f"- **{series_link}**{official_series_link(series)}",
                f"  {series.description}",
                f"  **Series:** {recurrence_badge(series.recurrence)}",
            ]
        )
        next_event = next_series_event_cell(series_items, series_undated, today)
        if next_event:
            lines.append(f"  **Next:** {next_event}")
        lines.append("")
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
    badges = [
        *[event_type_badge(event_type) for event_type in common_event_types(conferences)],
        cfp_badge(deadlines, event_start, reference_date),
    ]
    badges = [badge for badge in badges if badge]
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
        return ""
    return shield_badge("CFP", "closed", "lightgrey")


def recurrence_badge(recurrence: str) -> str:
    colors = {
        "recurring": "blue",
        "one-off": "lightgrey",
        "unknown": "yellow",
    }
    return shield_badge("recurrence", recurrence, colors.get(recurrence, "lightgrey"))


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
    undated_events: Iterable[UndatedEvent],
    reference_date: date,
) -> str:
    upcoming = sorted(
        (item for item in items if item.kind == "event" and item.start >= reference_date),
        key=lambda item: (item.start, item.summary.lower()),
    )
    if upcoming:
        item = upcoming[0]
        return (
            f"{shield_badge('next', compact_date_range_with_year(item.start, item.end_exclusive), 'brightgreen')} "
            f"{markdown_link(item.summary, item.url)}"
        )
    undated = sorted(undated_events, key=lambda item: item.title.lower())
    if undated:
        return f"{shield_badge('next', 'TBD', 'yellow')} {markdown_link(undated[0].title, undated[0].url)}"
    return ""


def series_page_url(slug: str, config: BuildConfig) -> str:
    return f"{config.site_url}events/series/{slug}.html"


def official_series_link(series: SeriesMetadata) -> str:
    if not series.website:
        return ""
    return f" {markdown_link('↗', series.website)}"


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


def event_markdown_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
) -> tuple[MarkdownPage, ...]:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
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
    undated_events: tuple[UndatedEvent, ...],
    *,
    item_keys: Callable[[CalendarItem], Iterable[str]],
    undated_keys: Callable[[UndatedEvent], Iterable[str]],
) -> list[MarkdownPage]:
    dated_by_key: dict[str, list[CalendarItem]] = {}
    undated_by_key: dict[str, list[UndatedEvent]] = {}

    for item in items:
        for key in item_keys(item):
            dated_by_key.setdefault(key, []).append(item)
    for item in undated_events:
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


def render_event_markdown(
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent] | date = (),
    reference_date: date | None = None,
    title: str = "Events",
) -> str:
    if isinstance(undated_events, date):
        reference_date = undated_events
        undated_events = ()
    today = reference_date or date.today()
    rows = event_markdown_rows(items)
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
    append_undated_event_section(lines, tuple(undated_events))
    append_timeline_markdown_section(
        lines,
        "Past Events",
        past,
        "No tracked past events.",
        reverse_years=True,
        reference_date=today,
    )

    return "\n".join(lines).rstrip() + "\n"


def render_event_html(
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent] | date = (),
    reference_date: date | None = None,
    title: str = "Events",
) -> str:
    if isinstance(undated_events, date):
        reference_date = undated_events
        undated_events = ()
    today = reference_date or date.today()
    rows = event_markdown_rows(items)
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
    append_undated_event_html_section(lines, tuple(undated_events))
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


def has_open_submission_deadline(row: EventMarkdownRow, reference_date: date) -> bool:
    return any(deadline.start >= reference_date for deadline in row[7])


def append_submission_opportunities_section(
    lines: list[str],
    rows: list[EventMarkdownRow],
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
            f"{event_scope_cell(title, conferences)} | "
            f"{event_location_cell(location, conferences)} | "
            f"{format_last_checked(last_checked)} |"
        )
    lines.append("")


def submission_opportunity_rows(
    rows: list[EventMarkdownRow], reference_date: date
) -> list[tuple[CalendarItem, EventMarkdownRow]]:
    opportunities = [(deadline, row) for row in rows for deadline in row[7] if deadline.start >= reference_date]
    return sorted(opportunities, key=lambda value: (value[0].start, value[1][0], value[1][2].lower()))


def append_submission_opportunities_html_section(
    lines: list[str],
    rows: list[EventMarkdownRow],
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
                event_scope_html_cell(title, conferences),
                event_location_html_cell(location, conferences),
                escape_html(format_last_checked(last_checked)),
            ),
        )
    append_html_table_end(lines)


def append_timeline_markdown_section(
    lines: list[str],
    title: str,
    rows: list[EventMarkdownRow],
    empty_message: str,
    *,
    reverse_years: bool = False,
    reference_date: date,
) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend([empty_message, ""])
        return

    by_year: dict[int, list[EventMarkdownRow]] = {}
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
                f"{event_cell(title, url, conferences)} | "
                f"{submission_status_cell(deadlines, conferences, start, reference_date)} | "
                f"{event_location_cell(location, conferences)} | "
                f"{format_last_checked(last_checked)} |"
            )
        lines.append("")


def append_timeline_html_section(
    lines: list[str],
    title: str,
    rows: list[EventMarkdownRow],
    empty_message: str,
    reference_date: date,
    *,
    reverse_years: bool = False,
) -> None:
    lines.append(f'    <h2 id="{html_id(title)}">{escape_html(title)}</h2>')
    if not rows:
        lines.append(f"    <p>{escape_html(empty_message)}</p>")
        return

    by_year: dict[int, list[EventMarkdownRow]] = {}
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
                    event_html_cell(title, url, conferences),
                    submission_status_html_cell(deadlines, conferences, start, reference_date),
                    event_location_html_cell(location, conferences),
                    escape_html(format_last_checked(last_checked)),
                ),
            )
        append_html_table_end(lines)


def append_undated_event_section(lines: list[str], rows: tuple[UndatedEvent, ...]) -> None:
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


def append_undated_event_html_section(lines: list[str], rows: tuple[UndatedEvent, ...]) -> None:
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


def event_markdown_rows(
    items: Iterable[CalendarItem],
) -> list[EventMarkdownRow]:
    items_tuple = tuple(items)
    groups: dict[tuple[str, str], list[CalendarItem]] = {}
    for item in items_tuple:
        if item.kind != "event":
            continue
        if item.co_location_group:
            key = ("group", item.co_location_group)
        else:
            key = ("event", item.uid)
        groups.setdefault(key, []).append(item)

    rows: list[EventMarkdownRow] = []
    for key, group_items in groups.items():
        order = co_location_series_order(group_items) if key[0] == "group" else ()
        conferences = tuple(sorted(group_items, key=lambda item: event_item_sort_key(item, order)))
        start = min(item.start for item in conferences)
        end_exclusive = max(item.end_exclusive for item in conferences)
        title, url = event_row_title(key, conferences)
        location = common_value(item.location for item in conferences)
        deadlines = submission_deadlines_for_events(items_tuple, conferences)
        last_checked = latest_date(
            item.last_checked for item in (*conferences, *deadlines) if item.last_checked is not None
        )
        rows.append((start, end_exclusive, title, url, location, last_checked, conferences, deadlines))
    return rows


def submission_deadlines_for_events(
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
                and deadline_matches_events(item, conferences)
            ),
            key=lambda item: (item.start, item.series_slug, item.summary),
        )
    )


def is_submission_deadline(item: CalendarItem) -> bool:
    text = f"{item.kind} {item.summary}".lower()
    return any(keyword in text for keyword in SUBMISSION_DEADLINE_KEYWORDS)


def deadline_matches_events(deadline: CalendarItem, conferences: tuple[CalendarItem, ...]) -> bool:
    return any(deadline_matches_event(deadline, conference) for conference in conferences)


def deadline_matches_event(deadline: CalendarItem, conference: CalendarItem) -> bool:
    if deadline.series_slug != conference.series_slug:
        return False
    if conference.co_location_group:
        return deadline.co_location_group == conference.co_location_group
    return deadline.uid.startswith(f"{calendar_item_uid_prefix(conference)}-deadline-")


def calendar_item_uid_prefix(item: CalendarItem) -> str:
    return item.uid.rsplit("@", 1)[0]


def co_location_series_order(items: Iterable[CalendarItem]) -> tuple[str, ...]:
    return next((item.co_location_series for item in items if item.co_location_series), ())


def event_item_sort_key(item: CalendarItem, series_order: tuple[str, ...]) -> tuple[int, date, str, str]:
    if item.series_slug in series_order:
        order = series_order.index(item.series_slug)
    else:
        order = len(series_order)
    return order, item.start, item.series, item.summary


def event_row_title(key: tuple[str, str], conferences: tuple[CalendarItem, ...]) -> tuple[str, str]:
    primary = conferences[0]
    return primary.summary, primary.url


def event_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    event = markdown_link(title, url)
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        contained = ", ".join(markdown_link(short_event_label(item), item.url) for item in conferences[1:])
        return f"{event} ({contained})"
    return event


def event_html_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    event = html_link(title, url)
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        contained = ", ".join(html_link(short_event_label(item), item.url) for item in conferences[1:])
        return f"{event} ({contained})"
    return event


def event_scope_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        return ", ".join(markdown_link(short_event_label(item), item.url) for item in conferences[1:])
    return escape_markdown_table(event_scope_label(title) or "TBD")


def event_scope_html_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        return ", ".join(html_link(short_event_label(item), item.url) for item in conferences[1:])
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
        prefix = deadline_event_label(deadline, conferences)
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


def deadline_event_label(deadline: CalendarItem, conferences: tuple[CalendarItem, ...]) -> str:
    for conference in conferences:
        if conference.series_slug == deadline.series_slug:
            return short_event_label(conference)
    return ""


def event_location_cell(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    if not location:
        return "TBD"
    maps_url = google_maps_url(location, conferences)
    return "<br>".join(markdown_link(line, maps_url) for line in event_location_lines(location, conferences))


def event_location_html_cell(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    if not location:
        return "TBD"
    maps_url = google_maps_url(location, conferences)
    return "<br>".join(html_link(line, maps_url) for line in event_location_lines(location, conferences))


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


def event_location_lines(location: str, conferences: tuple[CalendarItem, ...]) -> tuple[str, ...]:
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


def short_event_label(item: CalendarItem) -> str:
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


def write_rss_feed(site_root: Path, items: Iterable[CalendarItem], updated_at: datetime, config: BuildConfig) -> None:
    (site_root / "events.xml").write_text(render_rss_feed(items, updated_at, config), encoding="utf-8", newline="\n")


def render_rss_feed(items: Iterable[CalendarItem], updated_at: datetime, config: BuildConfig) -> str:
    rss_date = format_datetime(normalize_datetime(updated_at), usegmt=True)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "  <channel>",
        "    <title>Event Updates</title>",
        f"    <link>{escape_xml(config.site_url)}</link>",
        f'    <atom:link href="{escape_xml(config.site_url)}events.xml" rel="self" type="application/rss+xml" />',
        f"    <description>{escape_xml(config.disclaimer)}</description>",
        "    <language>en</language>",
        f"    <pubDate>{rss_date}</pubDate>",
        f"    <lastBuildDate>{rss_date}</lastBuildDate>",
        "    <ttl>360</ttl>",
    ]

    for item in items:
        lines.extend(render_rss_item(item, config))

    lines.extend(["  </channel>", "</rss>"])
    return "\n".join(lines) + "\n"


def render_rss_item(item: CalendarItem, config: BuildConfig) -> list[str]:
    tags = unique_values(
        tag
        for tag in (
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
        f"      <link>{escape_xml(item.url or config.site_url)}</link>",
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


def write_site_index(output_dir: Path, feeds: list[Feed], config: BuildConfig) -> None:
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
    <title>{escape_html(config.site_title)}</title>
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
    <h1>{escape_html(config.site_title)}</h1>
    <p>Subscribe to generated iCalendar feeds for tracked events and deadlines.</p>
    <p>Primary feed: <a href="calendars/all.ics"><code>calendars/all.ics</code></a></p>
    <p>Event list: <a href="events/all.html"><code>events/all.html</code></a></p>
    <p>RSS event stream: <a href="events.xml"><code>events.xml</code></a></p>
    <p>GitHub repository: <a href="{escape_html(config.repository_url)}">{escape_html(config.repository_url)}</a></p>
    <h2>Feeds</h2>
    <ul>
{links}
    </ul>
    <h2>Event Lists</h2>
    <ul>
{conference_links}
    </ul>
    <h2>Disclaimer</h2>
    <p class="notice">{escape_html(config.disclaimer)}</p>
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


def clean_stale_feeds(
    output_dir: Path,
    expected_paths: set[Path],
    config: BuildConfig,
    updated_at: datetime,
) -> int:
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
            path.write_text(
                render_calendar("Removed Calendar Feed", (), config, updated_at),
                encoding="utf-8",
                newline="\n",
            )
        cleaned += 1
    return cleaned


def clean_stale_event_docs(site_root: Path, expected_paths: set[Path]) -> int:
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


def format_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def format_datetime_ics(value: datetime) -> str:
    return normalize_datetime(value).strftime("%Y%m%dT%H%M%SZ")


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
