from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date
from urllib.parse import quote

from industrial_events import data
from industrial_events.models import CalendarItem, EventRow, SubmissionOpportunity
from industrial_events.render_utils import escape_html, escape_markdown_table, html_link, markdown_link
from industrial_events.validation import SUBMISSION_DEADLINE_KEYWORDS

common_value = data.common_value
event_scope_label = data.event_scope_label
latest_date = data.latest_date
unique_values = data.unique_values


def event_rows(items: Iterable[CalendarItem]) -> tuple[EventRow, ...]:
    items_tuple = tuple(items)
    grouped_events = _group_conferences(items_tuple)
    submission_deadlines = tuple(
        item for item in items_tuple if item.kind.startswith("deadline-") and is_submission_deadline(item)
    )

    rows: list[EventRow] = []
    for conferences in grouped_events.values():
        ordered = _ordered_conferences(conferences)
        deadlines = submission_deadlines_for_events(submission_deadlines, ordered)
        rows.append(
            EventRow(
                start=min(item.start for item in ordered),
                end_exclusive=max(item.end_exclusive for item in ordered),
                title=ordered[0].summary,
                url=ordered[0].url,
                location=common_value(item.location for item in ordered),
                last_checked=latest_date(
                    item.last_checked for item in (*ordered, *deadlines) if item.last_checked is not None
                ),
                conferences=ordered,
                deadlines=deadlines,
            )
        )
    return tuple(rows)


def submission_opportunity_rows(
    rows: Iterable[EventRow],
    reference_date: date,
) -> tuple[SubmissionOpportunity, ...]:
    opportunities = [
        SubmissionOpportunity(deadline=deadline, row=row)
        for row in rows
        for deadline in row.deadlines
        if deadline.start >= reference_date
    ]
    return tuple(sorted(opportunities, key=lambda item: (item.deadline.start, item.row.start, item.row.title.lower())))


def submission_deadlines_for_events(
    items: tuple[CalendarItem, ...],
    conferences: tuple[CalendarItem, ...],
) -> tuple[CalendarItem, ...]:
    deadlines = (
        item
        for item in items
        if item.kind.startswith("deadline-")
        and is_submission_deadline(item)
        and deadline_matches_events(item, conferences)
    )
    return tuple(sorted(deadlines, key=lambda item: (item.start, item.series_slug, item.summary)))


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


def event_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    return _event_cell(title, url, conferences, markdown_link)


def event_html_cell(title: str, url: str, conferences: tuple[CalendarItem, ...]) -> str:
    return _event_cell(title, url, conferences, html_link)


def _event_cell(
    title: str,
    url: str,
    conferences: tuple[CalendarItem, ...],
    link: Callable[[str, str], str],
) -> str:
    event = link(title, url)
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        related = ", ".join(link(short_event_label(item), item.url) for item in conferences[1:])
        return f"{event} ({related})"
    return event


def event_scope_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    return _event_scope_cell(title, conferences, markdown_link, escape_markdown_table)


def event_scope_html_cell(title: str, conferences: tuple[CalendarItem, ...]) -> str:
    return _event_scope_cell(title, conferences, html_link, escape_html)


def _event_scope_cell(
    title: str,
    conferences: tuple[CalendarItem, ...],
    link: Callable[[str, str], str],
    escape: Callable[[str], str],
) -> str:
    primary = conferences[0]
    if primary.co_location_group and len(conferences) > 1:
        return ", ".join(link(short_event_label(item), item.url) for item in conferences[1:])
    return escape(event_scope_label(title) or "TBD")


def submission_status_cell(
    deadlines: tuple[CalendarItem, ...],
    conferences: tuple[CalendarItem, ...],
    event_start: date,
    reference_date: date,
) -> str:
    open_deadlines = tuple(deadline for deadline in deadlines if deadline.start >= reference_date)
    if open_deadlines:
        links = (
            markdown_link(submission_deadline_label(deadline, conferences), deadline.url) for deadline in open_deadlines
        )
        return "Open: " + "<br>".join(links)
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
        links = (
            html_link(submission_deadline_label(deadline, conferences), deadline.url) for deadline in open_deadlines
        )
        return status_badge("Open", "open") + "<br>" + "<br>".join(links)
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
    return _event_location_cell(location, conferences, markdown_link)


def event_location_html_cell(location: str, conferences: tuple[CalendarItem, ...]) -> str:
    return _event_location_cell(location, conferences, html_link)


def _event_location_cell(
    location: str,
    conferences: tuple[CalendarItem, ...],
    link: Callable[[str, str], str],
) -> str:
    if not location:
        return "TBD"
    maps_url = google_maps_url(location, conferences)
    return "<br>".join(link(line, maps_url) for line in event_location_lines(location, conferences))


def undated_location_cell(location: str) -> str:
    return _undated_location_cell(location, markdown_link)


def undated_location_html_cell(location: str) -> str:
    return _undated_location_cell(location, html_link)


def _undated_location_cell(location: str, link: Callable[[str, str], str]) -> str:
    if not location:
        return "TBD"
    maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(location, safe=',')}"
    return link(location, maps_url)


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


def calendar_item_uid_prefix(item: CalendarItem) -> str:
    return item.uid.rsplit("@", 1)[0]


def _group_conferences(items: tuple[CalendarItem, ...]) -> dict[tuple[str, str], list[CalendarItem]]:
    groups: dict[tuple[str, str], list[CalendarItem]] = {}
    for item in items:
        if item.kind != "event":
            continue
        key = ("group", item.co_location_group) if item.co_location_group else ("event", item.uid)
        groups.setdefault(key, []).append(item)
    return groups


def _ordered_conferences(conferences: list[CalendarItem]) -> tuple[CalendarItem, ...]:
    series_order = next((item.co_location_series for item in conferences if item.co_location_series), ())
    return tuple(sorted(conferences, key=lambda item: _event_item_sort_key(item, series_order)))


def _event_item_sort_key(item: CalendarItem, series_order: tuple[str, ...]) -> tuple[int, date, str, str]:
    order = series_order.index(item.series_slug) if item.series_slug in series_order else len(series_order)
    return order, item.start, item.series, item.summary
