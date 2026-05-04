from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "conferences"
DEFAULT_OUTPUT_DIR = ROOT / "public" / "calendars"
PRODID = "-//Conference Calendars//EN"
DTSTAMP = "19700101T000000Z"
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
    url: str = ""
    description: str = ""
    latitude: float | None = None
    longitude: float | None = None
    co_location_group: str = ""
    co_location_series: tuple[str, ...] = ()


@dataclass(frozen=True)
class Feed:
    path: Path
    name: str
    items: tuple[CalendarItem, ...]


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
    args = parser.parse_args(argv)

    try:
        feeds = build_calendars(args.source, args.output)
    except CalendarBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Generated {len(feeds)} calendar feed(s) in {args.output}")
    return 0


def build_calendars(source_dir: Path, output_dir: Path) -> list[Feed]:
    items = load_items(source_dir)
    feeds = build_feeds(items, output_dir)
    clean_stale_feeds(output_dir, {feed.path.resolve() for feed in feeds})
    output_dir.mkdir(parents=True, exist_ok=True)

    for feed in feeds:
        feed.path.parent.mkdir(parents=True, exist_ok=True)
        feed.path.write_text(render_calendar(feed.name, feed.items), encoding="utf-8", newline="\n")

    write_index(output_dir, feeds)
    return feeds


def load_items(source_dir: Path) -> list[CalendarItem]:
    if not source_dir.exists():
        return []

    items: list[CalendarItem] = []
    seen_slugs: dict[str, Path] = {}

    for path in sorted(source_files(source_dir)):
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
        events = data.get("events", [])
        if not isinstance(events, list):
            raise CalendarBuildError(f"{path}: events must be a list of YAML objects")

        for index, event in enumerate(events, start=1):
            if not isinstance(event, dict):
                raise CalendarBuildError(f"{path}: event {index} must be a YAML object")
            validate_unknown_keys(path, event, EVENT_KEYS, f"event {index}")
            items.extend(
                items_for_event(path, domain, series, slug, categories, topics, website, series_sources, index, event)
            )

    return sorted(items, key=lambda item: (item.start, item.series_slug, item.kind, item.summary))


def items_for_event(
    path: Path,
    domain: str,
    series: str,
    slug: str,
    categories: tuple[str, ...],
    topics: tuple[str, ...],
    website: str,
    series_sources: tuple[str, ...],
    event_index: int,
    event: dict,
) -> list[CalendarItem]:
    event_name = require_str(path, event, "name", f"event {event_index}")
    start = require_date(path, event, "start", f"event {event_index}")
    end = require_date(path, event, "end", f"event {event_index}")
    if end < start:
        raise CalendarBuildError(f"{path}: event {event_index} end must be on or after start")

    country = require_str(path, event, "country", f"event {event_index}").lower()
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
        url=url,
        description=description,
        latitude=latitude,
        longitude=longitude,
        co_location_group=co_location.group,
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
                co_location_group=co_location.group,
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
        f"X-WR-CALNAME:{escape_text(name)}",
        f"X-WR-CALDESC:{escape_text(DISCLAIMER)}",
    ]

    for item in items:
        lines.extend(render_event(item))

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def render_event(item: CalendarItem) -> list[str]:
    tags = ["conference", item.kind, item.series_slug, item.country, *item.categories, *item.topics]
    lines = [
        "BEGIN:VEVENT",
        f"UID:{escape_text(item.uid)}",
        f"DTSTAMP:{DTSTAMP}",
        f"DTSTART;VALUE=DATE:{format_date(item.start)}",
        f"DTEND;VALUE=DATE:{format_date(item.end_exclusive)}",
        f"SUMMARY:{escape_text(item.summary)}",
        f"STATUS:{item.status}",
        "TRANSP:TRANSPARENT",
        f"CATEGORIES:{','.join(escape_text(tag) for tag in tags)}",
        f"X-CONFERENCE-SERIES:{escape_text(item.series)}",
        f"X-CONFERENCE-SERIES-SLUG:{escape_text(item.series_slug)}",
        f"X-CONFERENCE-DOMAIN:{escape_text(item.domain)}",
        f"X-CONFERENCE-COUNTRY:{escape_text(item.country.upper())}",
    ]
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


def clean_stale_feeds(output_dir: Path, expected_paths: set[Path]) -> None:
    if not output_dir.exists():
        return
    for path in output_dir.rglob("*.ics"):
        if path.resolve() in expected_paths:
            continue
        try:
            path.unlink()
        except PermissionError:
            path.write_text(render_calendar("Removed Calendar Feed", ()), encoding="utf-8", newline="\n")


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
        for key in ("type", "scope"):
            source_type = optional_str(source, key)
            if source_type and not SLUG_RE.fullmatch(source_type):
                raise CalendarBuildError(f"{path}: {source_label} field {key!r} must be a lowercase slug")
    return tuple(urls)


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
