from __future__ import annotations

import re
from calendar import month_name
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from industrial_events.config import BuildConfig
from industrial_events.data import (
    common_value,
    load_series_metadata,
    unique_values,
)
from industrial_events.event_rows import (
    event_cell,
    event_rows,
    event_scope_cell,
    google_maps_url,
    submission_deadline_label,
    submission_opportunity_rows,
)
from industrial_events.models import CalendarItem, SeriesMetadata, SourcePage, UndatedEvent
from industrial_events.render_utils import escape_markdown_table, format_last_checked, markdown_link
from industrial_events.sources import load_source_pages

README_UPCOMING_START = "<!-- generated:upcoming-events:start -->"
README_UPCOMING_END = "<!-- generated:upcoming-events:end -->"
README_SUBMISSION_START = "<!-- generated:submission-opportunities:start -->"
README_SUBMISSION_END = "<!-- generated:submission-opportunities:end -->"
README_SERIES_START = "<!-- generated:series-overview:start -->"
README_SERIES_END = "<!-- generated:series-overview:end -->"
README_ONE_TIME_START = "<!-- generated:one-time-events:start -->"
README_ONE_TIME_END = "<!-- generated:one-time-events:end -->"
README_SINGLE_EVENT_START = "<!-- generated:single-event-records:start -->"
README_SINGLE_EVENT_END = "<!-- generated:single-event-records:end -->"
README_SOURCES_START = "<!-- generated:overview-sources:start -->"
README_SOURCES_END = "<!-- generated:overview-sources:end -->"


def write_readme_overview(
    readme_path: Path,
    source_dir: Path,
    sources_dir: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None = None,
    series_metadata: Iterable[SeriesMetadata] | None = None,
    source_pages: Iterable[SourcePage] | None = None,
) -> None:
    if not readme_path.exists():
        return

    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    metadata_tuple = tuple(series_metadata) if series_metadata is not None else tuple(load_series_metadata(source_dir))
    pages_tuple = tuple(source_pages) if source_pages is not None else tuple(load_source_pages(sources_dir))

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
        render_readme_series_overview(metadata_tuple, items_tuple, undated_tuple, config, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_ONE_TIME_START,
        README_ONE_TIME_END,
        render_readme_one_time_events(metadata_tuple, items_tuple, undated_tuple, config, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SINGLE_EVENT_START,
        README_SINGLE_EVENT_END,
        render_readme_single_event_records(metadata_tuple, items_tuple, undated_tuple, config, reference_date),
    )
    updated = replace_marked_section(
        updated,
        README_SOURCES_START,
        README_SOURCES_END,
        render_readme_overview_sources(pages_tuple),
    )
    if updated != current:
        readme_path.write_text(updated, encoding="utf-8", newline="\n")


def render_readme_upcoming_events(
    items: Iterable[CalendarItem],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    window_start = today.replace(day=1)
    rows = [row for row in event_rows(items) if row.end_exclusive > window_start]
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
        for row in sorted(rows, key=lambda item: (item.start, item.title.lower())):
            if row.start.year != current_year:
                if current_year is not None:
                    lines.append("")
                current_year = row.start.year
                current_month = None
                lines.extend([f"### {row.start.year}", ""])
            if row.start.month != current_month:
                if current_month is not None:
                    lines.append("")
                current_month = row.start.month
                lines.extend([f"#### {month_name[row.start.month]}", ""])
            badges = readme_event_title_badges(row.conferences, row.deadlines, row.start, today)
            detail = readme_event_detail(row.conferences, row.location, today)
            tags = readme_tags_detail(row.conferences)
            badge_suffix = f" {badges}" if badges else ""
            lines.append(
                f"- **{compact_date_range(row.start, row.end_exclusive)}**: "
                f"{event_cell(row.title, row.url, row.conferences)}{official_resource_icons(row.conferences)}"
                f"{badge_suffix}"
            )
            if detail:
                lines.append(f"  - {detail}")
            if tags:
                lines.append(f"  - {tags}")
    lines.extend(["", README_UPCOMING_END])
    return "\n".join(lines)


def render_readme_submission_opportunities(
    items: Iterable[CalendarItem],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    today = reference_date or date.today()
    opportunities = submission_opportunity_rows(event_rows(items), today)
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
        for opportunity in opportunities:
            row = opportunity.row
            deadline_label = submission_deadline_label(opportunity.deadline, row.conferences)
            lines.append(
                "| "
                f"{markdown_link(deadline_label, opportunity.deadline.url)} | "
                f"{markdown_link(row.title, row.url)}{official_resource_icons(row.conferences)} | "
                f"{escape_markdown_table(compact_date_range_with_year(row.start, row.end_exclusive))} | "
                f"{event_scope_cell(row.title, row.conferences)} | "
                f"{format_last_checked(row.last_checked)} |"
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
    lines = [README_SERIES_START, ""]

    recurring = (item for item in metadata if item.recurrence != "one-off")
    for series in sorted(recurring, key=lambda item: item.series.lower()):
        series_items, series_undated = series_records(series.slug, items_tuple, undated_tuple)
        if observed_event_count(series_items, series_undated) < 2:
            continue
        series_link = markdown_link(series.series, series_page_url(series.slug, config))
        lines.extend(
            [
                f"- **{series_link}**{official_series_link(series)}",
                f"  {markdown_text(series.description)}",
                f"  {series_badges(series, series_items, series_undated, today)}",
            ]
        )
        next_event = next_series_event_cell(series.recurrence, series_items, series_undated, today)
        if next_event:
            lines.append(f"  **Next:** {next_event}")
        lines.append("")
    lines.append(README_SERIES_END)
    return "\n".join(lines)


def render_readme_one_time_events(
    metadata: Iterable[SeriesMetadata],
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    one_time = [series for series in metadata if series.recurrence == "one-off"]
    return render_single_series_group(
        one_time,
        items,
        undated_events,
        config,
        reference_date,
        marker=README_ONE_TIME_START,
        marker_end=README_ONE_TIME_END,
        empty_message="No one-time events are tracked separately right now.",
    )


def render_readme_single_event_records(
    metadata: Iterable[SeriesMetadata],
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None = None,
) -> str:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    singles: list[SeriesMetadata] = []
    for series in metadata:
        if series.recurrence == "one-off":
            continue
        series_items, series_undated = series_records(series.slug, items_tuple, undated_tuple)
        if observed_event_count(series_items, series_undated) == 1:
            singles.append(series)
    return render_single_series_group(
        sorted(singles, key=lambda item: item.series.lower()),
        items_tuple,
        undated_tuple,
        config,
        reference_date,
        marker=README_SINGLE_EVENT_START,
        marker_end=README_SINGLE_EVENT_END,
        empty_message="No single-event records are tracked separately right now.",
    )


def render_single_series_group(
    metadata: Iterable[SeriesMetadata],
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    config: BuildConfig,
    reference_date: date | None,
    *,
    marker: str,
    marker_end: str,
    empty_message: str,
) -> str:
    today = reference_date or date.today()
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    lines = [marker, ""]

    metadata_tuple = tuple(metadata)
    if not metadata_tuple:
        lines.append(empty_message)
    else:
        for series in metadata_tuple:
            series_items, series_undated = series_records(series.slug, items_tuple, undated_tuple)
            series_link = markdown_link(series.series, series_page_url(series.slug, config))
            lines.extend(
                [
                    f"- **{series_link}**{official_series_link(series)}",
                    f"  {markdown_text(series.description)}",
                ]
            )
            badges = useful_single_event_badges(series, series_items, series_undated, today)
            if badges:
                lines.append(f"  {badges}")
            next_event = next_series_event_cell(series.recurrence, series_items, series_undated, today)
            if next_event:
                lines.append(f"  **Event:** {next_event}")
            lines.append("")
        if lines[-1] == "":
            lines.pop()
    lines.extend(["", marker_end])
    return "\n".join(lines)


def render_readme_overview_sources(sources: Path | Iterable[SourcePage]) -> str:
    pages = tuple(load_source_pages(sources) if isinstance(sources, Path) else sources)
    lines = [
        README_SOURCES_START,
        "",
        "Discovery sources help find and monitor events. "
        "Event-specific verification dates are tracked with the event data.",
        "",
    ]
    if not pages:
        lines.append("No discovery sources are tracked yet.")
    else:
        lines.extend(f"- {markdown_link(page.name, page.url)}" for page in pages)
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


def markdown_text(value: str) -> str:
    return (
        value.replace("\r\n", " ")
        .replace("\n", " ")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def limited_tag_list(values: Iterable[str], limit: int = 6) -> str:
    tags = unique_values(value for value in values if value)
    visible = tags[:limit]
    result = " ".join(f"`{escape_markdown_table(tag)}`" for tag in visible)
    if len(tags) > limit:
        result = f"{result} +{len(tags) - limit} more"
    return result or "TBD"


def readme_event_detail(
    conferences: tuple[CalendarItem, ...],
    fallback_location: str,
    reference_date: date,
) -> str:
    parts = [
        readme_location_detail(conferences, fallback_location),
        readme_venue_detail(conferences, reference_date),
        readme_event_types_detail(conferences),
    ]
    return " | ".join(part for part in parts if part)


def readme_location_detail(conferences: tuple[CalendarItem, ...], fallback_location: str) -> str:
    label = compact_location(conferences, fallback_location)
    flag = country_flag_icon(conferences)
    flag_suffix = f" {flag}" if flag else ""
    if fallback_location:
        return f"**Location:**{flag_suffix} [{label}](<{google_maps_url(fallback_location, conferences)}>)"
    return f"**Location:**{flag_suffix} {label}"


def readme_venue_detail(conferences: tuple[CalendarItem, ...], reference_date: date) -> str:
    venue = common_value(item.venue for item in conferences)
    if venue:
        return f"Venue: {markdown_text(venue)}"
    if any(not item.venue and item.start >= reference_date for item in conferences):
        return "Venue: TBD"
    return ""


def readme_event_types_detail(conferences: Iterable[CalendarItem]) -> str:
    event_types = common_event_types(conferences)
    label = "Type" if len(event_types) == 1 else "Types"
    return f"{label}: {', '.join(event_types)}" if event_types else ""



def readme_tags_detail(conferences: Iterable[CalendarItem]) -> str:
    tags = limited_tag_list(common_tags(conferences))
    return f"**Tags:** {tags}" if tags else ""


def readme_event_title_badges(
    conferences: tuple[CalendarItem, ...],
    deadlines: tuple[CalendarItem, ...],
    event_start: date,
    reference_date: date,
) -> str:
    parts = [
        venue_badge(conferences, reference_date),
        readme_event_badges(conferences, deadlines, event_start, reference_date),
    ]
    return " ".join(part for part in parts if part)


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
    return " ".join(badge for badge in badges if badge)


def venue_badge(conferences: tuple[CalendarItem, ...], reference_date: date) -> str:
    if any(not item.venue and item.start >= reference_date for item in conferences):
        return shield_badge("venue", "TBD", "yellow")
    return ""


def event_type_badge(event_type: str) -> str:
    colors = {"conference": "blueviolet", "exhibition": "ff69b4", "trade-fair": "red"}
    return shield_badge("type", event_type, colors.get(event_type, "black"))


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


def series_badges(
    series: SeriesMetadata,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    reference_date: date,
) -> str:
    items_tuple = tuple(item for item in items if item.kind == "event")
    return recurrence_badge(series.recurrence, (*items_tuple, *tuple(undated_events)))


def useful_single_event_badges(
    series: SeriesMetadata,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    reference_date: date,
) -> str:
    badges = series_badges(series, items, undated_events, reference_date)
    if badges == shield_badge("recurrence", "recurring", "blue"):
        return ""
    return badges


def recurrence_badge(recurrence: str, items: Iterable[CalendarItem | UndatedEvent] = ()) -> str:
    if recurrence == "one-off":
        return shield_badge("event", "one-time", "lightgrey")
    if recurrence == "unknown":
        return shield_badge("recurrence", "unknown", "yellow")
    label, cadence = observed_cadence(items)
    if label and cadence:
        return shield_badge(label, cadence, "blue")
    return shield_badge("recurrence", recurrence, "blue")


def observed_cadence(items: Iterable[CalendarItem | UndatedEvent]) -> tuple[str, str]:
    years = sorted({item_year(item) for item in items if item_year(item) is not None})
    if len(years) < 2:
        return "", ""
    if len(years) == 2:
        gap = years[1] - years[0]
        return ("span", "1 year") if gap == 1 else ("span", f"{gap} years")
    gaps = [later - earlier for earlier, later in zip(years, years[1:], strict=False)]
    if gaps and len(set(gaps)) == 1:
        gap = gaps[0]
        return "frequency", "1 year" if gap == 1 else f"{gap} years"
    return "frequency", "irregular"


def observed_years(items: Iterable[CalendarItem | UndatedEvent]) -> tuple[int, ...]:
    return tuple(sorted({item_year(item) for item in items if item_year(item) is not None}))


def observed_gap_years(items: Iterable[CalendarItem | UndatedEvent]) -> int | None:
    years = observed_years(items)
    if len(years) < 2:
        return None
    gaps = [later - earlier for earlier, later in zip(years, years[1:], strict=False)]
    if len(years) == 2:
        return gaps[0]
    if gaps and len(set(gaps)) == 1:
        return gaps[0]
    return None


def item_year(item: CalendarItem | UndatedEvent) -> int | None:
    if isinstance(item, CalendarItem):
        if item.kind != "event":
            return None
        return item.start.year
    match = re.search(r"\b(20\d{2}|19\d{2})\b", item.title)
    if not match:
        return None
    return int(match.group(1))


def observed_event_count(items: Iterable[CalendarItem], undated_events: Iterable[UndatedEvent]) -> int:
    years = {item.start.year for item in items if item.kind == "event"}
    years.update(year for event in undated_events if (year := item_year(event)) is not None)
    return len(years)


def shield_badge(label: str, message: str, color: str) -> str:
    alt = f"{label}: {message}"
    return (
        f"![{escape_markdown_image_alt(alt)}]"
        f"(https://img.shields.io/badge/{shield_path_part(label)}-{shield_path_part(message)}-{color})"
    )



def shield_path_part(value: str) -> str:
    return quote(value.replace("-", "--"), safe="")

def country_flag_icon(conferences: tuple[CalendarItem, ...]) -> str:
    country = common_value(item.country.upper() for item in conferences if item.country)
    if len(country) != 2 or not country.isalpha():
        return ""
    return f"![{country} flag](https://flagcdn.com/16x12/{country.lower()}.png)"


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
    recurrence: str,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
    reference_date: date,
) -> str:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    upcoming = sorted(
        (item for item in items_tuple if item.kind == "event" and item.end_exclusive > reference_date),
        key=lambda item: (item.start, item.summary.lower()),
    )
    if upcoming:
        item = upcoming[0]
        return (
            f"{shield_badge('next', compact_date_range_with_year(item.start, item.end_exclusive), 'brightgreen')} "
            f"{markdown_link(item.summary, item.url)}{official_resource_icons((item,))}"
        )
    if undated_tuple:
        item = sorted(undated_tuple, key=lambda event: event.title.lower())[0]
        return f"{shield_badge('next', 'TBD', 'yellow')} {markdown_link(item.title, item.url)}"
    probable_year = probable_next_year(recurrence, (*items_tuple, *undated_tuple), reference_date)
    if probable_year:
        return shield_badge("next", f"probably {probable_year}", "yellow")
    return ""


def probable_next_year(
    recurrence: str,
    items: Iterable[CalendarItem | UndatedEvent],
    reference_date: date,
) -> int | None:
    if recurrence != "recurring":
        return None
    items_tuple = tuple(items)
    gap = observed_gap_years(items_tuple)
    years = observed_years(items_tuple)
    if gap is None or not years:
        return None
    probable = years[-1] + gap
    while probable < reference_date.year:
        probable += gap
    return probable


def series_page_url(slug: str, config: BuildConfig) -> str:
    return f"{config.site_url}events/series/{slug}.html"


def official_series_link(series: SeriesMetadata) -> str:
    if not series.website:
        return ""
    return f" {markdown_link('↗', series.website)}"


def official_resource_icons(conferences: tuple[CalendarItem, ...]) -> str:
    if not conferences:
        return ""
    primary = conferences[0]
    icons: list[str] = []
    if primary.proceedings_url:
        icons.append(markdown_link("📘", primary.proceedings_url))
    if primary.program_url:
        icons.append(markdown_link("🗓", primary.program_url))
    if not icons:
        return ""
    return " " + " ".join(icons)


def series_records(
    slug: str,
    items: tuple[CalendarItem, ...],
    undated_events: tuple[UndatedEvent, ...],
) -> tuple[tuple[CalendarItem, ...], tuple[UndatedEvent, ...]]:
    return (
        tuple(item for item in items if item.series_slug == slug),
        tuple(item for item in undated_events if item.series_slug == slug),
    )
