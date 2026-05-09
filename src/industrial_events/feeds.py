from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import date, datetime
from email.utils import format_datetime
from pathlib import Path

from industrial_events.config import BuildConfig, normalize_datetime
from industrial_events.data import build_description, unique_values
from industrial_events.event_pages import conference_markdown_links
from industrial_events.models import CalendarItem, Feed
from industrial_events.render_utils import escape_html, format_date_range
from industrial_events.url_utils import safe_external_url

FEED_GROUPS: tuple[tuple[str, str, Callable[[CalendarItem], Iterable[str]]], ...] = (
    ("series", "Event Series", lambda item: (item.series_slug,)),
    ("category", "Event Category", lambda item: item.categories),
    ("event-type", "Event Type", lambda item: item.event_types),
    ("country", "Event Country", lambda item: (item.country,) if item.country else ()),
    ("domain", "Event Domain", lambda item: (item.domain,)),
    ("group", "Co-located Group", lambda item: (item.co_location_group,) if item.co_location_group else ()),
)


def build_feeds(items: Iterable[CalendarItem], output_dir: Path) -> list[Feed]:
    items_tuple = tuple(items)
    feeds = [Feed(output_dir / "all.ics", "All Events", items_tuple)]
    for folder, title_prefix, keys in FEED_GROUPS:
        feeds.extend(group_feeds(output_dir / folder, title_prefix, items_tuple, keys))
    return feeds


def group_feeds(
    output_dir: Path,
    title_prefix: str,
    items: tuple[CalendarItem, ...],
    keys: Callable[[CalendarItem], Iterable[str]],
) -> list[Feed]:
    grouped: dict[str, list[CalendarItem]] = {}
    for item in items:
        for key in keys(item):
            grouped.setdefault(key, []).append(item)
    return [
        Feed(output_dir / f"{key}.ics", f"{title_prefix}: {label_for_feed_key(title_prefix, key)}", tuple(grouped[key]))
        for key in sorted(grouped)
    ]


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
    timestamp = format_datetime_ics(updated_at)
    for item in items:
        lines.extend(render_event(item, timestamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"


def render_event(item: CalendarItem, timestamp: str) -> list[str]:
    tags = item_tags(item)
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
    item_url = safe_external_url(item.url)
    if item_url:
        lines.append(f"URL:{escape_text(item_url)}")
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
    lines = [
        "    <item>",
        f"      <title>{escape_xml(item.summary)}</title>",
        f"      <link>{escape_xml(safe_external_url(item.url) or config.site_url)}</link>",
        f'      <guid isPermaLink="false">{escape_xml(item.uid)}</guid>',
        f"      <description>{escape_xml(rss_item_description(item))}</description>",
    ]
    lines.extend(f"      <category>{escape_xml(tag)}</category>" for tag in item_tags(item))
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


def write_site_index(output_dir: Path, feeds: list[Feed], config: BuildConfig) -> None:
    site_root = output_dir.parent
    links = "\n".join(
        f'        <li><a href="{escape_html(feed.path.relative_to(site_root).as_posix())}">'
        f"{escape_html(feed.name)}</a> ({len(feed.items)} items)</li>"
        for feed in feeds
    )
    event_links = "\n".join(
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
{event_links}
    </ul>
    <h2>Disclaimer</h2>
    <p class="notice">{escape_html(config.disclaimer)}</p>
  </body>
</html>
"""
    (site_root / "index.html").write_text(page, encoding="utf-8", newline="\n")


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


def item_tags(item: CalendarItem) -> tuple[str, ...]:
    return unique_values(
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


def label_for_feed_key(title_prefix: str, key: str) -> str:
    if title_prefix == "Event Country":
        return key.upper()
    return key


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
