from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

from industrial_events.event_rows import (
    event_cell,
    event_html_cell,
    event_location_cell,
    event_location_html_cell,
    event_rows,
    event_scope_cell,
    event_scope_html_cell,
    submission_deadline_label,
    submission_opportunity_rows,
    submission_status_cell,
    submission_status_html_cell,
    undated_location_cell,
    undated_location_html_cell,
)
from industrial_events.models import CalendarItem, EventRow, Feed, MarkdownPage, UndatedEvent
from industrial_events.render_utils import (
    escape_html,
    escape_markdown_table,
    format_date_range,
    format_last_checked,
    html_id,
    html_link,
    markdown_link,
)

PAGE_SPLITS: tuple[
    tuple[
        str,
        str,
        Callable[[CalendarItem], Iterable[str]],
        Callable[[UndatedEvent], Iterable[str]],
    ],
    ...,
] = (
    ("series", "Event Series", lambda item: (item.series_slug,), lambda item: (item.series_slug,)),
    ("category", "Event Category", lambda item: item.categories, lambda item: item.categories),
    ("event-type", "Event Type", lambda item: item.event_types, lambda item: item.event_types),
    ("domain", "Event Domain", lambda item: (item.domain,), lambda item: (item.domain,)),
    (
        "group",
        "Co-located Group",
        lambda item: (item.co_location_group,) if item.co_location_group else (),
        lambda item: (item.co_location_group,) if item.co_location_group else (),
    ),
)


def write_event_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent] = (),
    reference_date: date | None = None,
) -> tuple[int, int]:
    pages = event_markdown_pages(site_root, items, undated_events)
    remove_legacy_conference_markdown(site_root)
    expected_paths = {page.path.resolve() for page in pages}
    expected_paths.update(page.path.with_suffix(".html").resolve() for page in pages)
    cleaned = clean_stale_event_docs(site_root, expected_paths)

    for page in pages:
        page.path.parent.mkdir(parents=True, exist_ok=True)
        page.path.write_text(
            render_event_markdown(page.items, page.undated_events, reference_date, title=page.title),
            encoding="utf-8",
            newline="\n",
        )
        page.path.with_suffix(".html").write_text(
            render_event_html(page.items, page.undated_events, reference_date, title=page.title),
            encoding="utf-8",
            newline="\n",
        )
    return cleaned, len(pages)


def event_markdown_pages(
    site_root: Path,
    items: Iterable[CalendarItem],
    undated_events: Iterable[UndatedEvent],
) -> tuple[MarkdownPage, ...]:
    items_tuple = tuple(items)
    undated_tuple = tuple(undated_events)
    pages = [MarkdownPage(site_root / "events" / "all.md", "Events", items_tuple, undated_tuple)]

    for split, title_prefix, item_keys, undated_keys in PAGE_SPLITS:
        pages.extend(
            markdown_split_pages(
                site_root,
                split,
                title_prefix,
                items_tuple,
                undated_tuple,
                item_keys=item_keys,
                undated_keys=undated_keys,
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

    return [
        MarkdownPage(
            site_root / "events" / split / f"{key}.md",
            f"{title_prefix}: {key}",
            tuple(dated_by_key.get(key, ())),
            tuple(undated_by_key.get(key, ())),
        )
        for key in sorted(set(dated_by_key) | set(undated_by_key))
    ]


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
    rows = event_rows(items)
    upcoming, past = _partition_rows(rows, today)

    lines = [
        f"# {title}",
        "",
        "Tracked events grouped by submission status and timing.",
        "",
        "Always verify important dates and details against the linked official event pages.",
        "",
    ]
    append_submission_opportunities_section(lines, rows, reference_date=today)
    append_timeline_markdown_section(lines, "Upcoming Events", upcoming, "No tracked upcoming events.", today)
    append_undated_event_section(lines, tuple(undated_events))
    append_timeline_markdown_section(
        lines,
        "Past Events",
        past,
        "No tracked past events.",
        today,
        reverse_years=True,
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
    rows = event_rows(items)
    upcoming, past = _partition_rows(rows, today)

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
    append_timeline_html_section(lines, "Past Events", past, "No tracked past events.", today, reverse_years=True)
    lines.extend(["  </body>", "</html>"])
    return "\n".join(lines) + "\n"


def append_submission_opportunities_section(
    lines: list[str],
    rows: Iterable[EventRow],
    *,
    reference_date: date,
) -> None:
    lines.extend(["## Submission Opportunities", ""])
    opportunities = submission_opportunity_rows(rows, reference_date)
    if not opportunities:
        lines.extend(["No tracked events with open submission deadlines.", ""])
        return

    table_rows: list[tuple[str, ...]] = []
    for opportunity in opportunities:
        row = opportunity.row
        deadline_label = submission_deadline_label(opportunity.deadline, row.conferences)
        table_rows.append(
            (
                markdown_link(deadline_label, opportunity.deadline.url),
                markdown_link(row.title, row.url),
                escape_markdown_table(format_date_range(row.start, row.end_exclusive)),
                event_scope_cell(row.title, row.conferences),
                event_location_cell(row.location, row.conferences),
                format_last_checked(row.last_checked),
            )
        )
    append_markdown_table(
        lines,
        ("Deadline", "Event", "Event Dates", "Scope / Co-located Events", "Location", "Last Checked"),
        table_rows,
    )
    lines.append("")


def append_submission_opportunities_html_section(
    lines: list[str],
    rows: Iterable[EventRow],
    *,
    reference_date: date,
) -> None:
    lines.extend(['    <h2 id="submission-opportunities">Submission Opportunities</h2>'])
    opportunities = submission_opportunity_rows(rows, reference_date)
    if not opportunities:
        lines.append("    <p>No tracked events with open submission deadlines.</p>")
        return

    table_rows: list[tuple[str, ...]] = []
    for opportunity in opportunities:
        row = opportunity.row
        table_rows.append(
            (
                html_link(submission_deadline_label(opportunity.deadline, row.conferences), opportunity.deadline.url),
                html_link(row.title, row.url),
                escape_html(format_date_range(row.start, row.end_exclusive)),
                event_scope_html_cell(row.title, row.conferences),
                event_location_html_cell(row.location, row.conferences),
                escape_html(format_last_checked(row.last_checked)),
            )
        )
    append_html_table(
        lines,
        ("Deadline", "Event", "Event Dates", "Scope / Co-located Events", "Location", "Last Checked"),
        table_rows,
    )


def append_timeline_markdown_section(
    lines: list[str],
    title: str,
    rows: tuple[EventRow, ...],
    empty_message: str,
    reference_date: date,
    *,
    reverse_years: bool = False,
) -> None:
    lines.extend([f"## {title}", ""])
    if not rows:
        lines.extend([empty_message, ""])
        return

    for year, year_rows in _rows_by_year(rows, reverse_years=reverse_years):
        lines.extend([f"### {year}", ""])
        append_markdown_table(
            lines,
            ("Dates", "Event", "Submission Status", "Location", "Last Checked"),
            (
                (
                    escape_markdown_table(format_date_range(row.start, row.end_exclusive)),
                    event_cell(row.title, row.url, row.conferences),
                    submission_status_cell(row.deadlines, row.conferences, row.start, reference_date),
                    event_location_cell(row.location, row.conferences),
                    format_last_checked(row.last_checked),
                )
                for row in year_rows
            ),
        )
        lines.append("")


def append_timeline_html_section(
    lines: list[str],
    title: str,
    rows: tuple[EventRow, ...],
    empty_message: str,
    reference_date: date,
    *,
    reverse_years: bool = False,
) -> None:
    lines.append(f'    <h2 id="{html_id(title)}">{escape_html(title)}</h2>')
    if not rows:
        lines.append(f"    <p>{escape_html(empty_message)}</p>")
        return

    for year, year_rows in _rows_by_year(rows, reverse_years=reverse_years):
        lines.append(f"    <h3>{year}</h3>")
        append_html_table(
            lines,
            ("Dates", "Event", "Submission Status", "Location", "Last Checked"),
            (
                (
                    escape_html(format_date_range(row.start, row.end_exclusive)),
                    event_html_cell(row.title, row.url, row.conferences),
                    submission_status_html_cell(row.deadlines, row.conferences, row.start, reference_date),
                    event_location_html_cell(row.location, row.conferences),
                    escape_html(format_last_checked(row.last_checked)),
                )
                for row in year_rows
            ),
        )


def append_undated_event_section(lines: list[str], rows: tuple[UndatedEvent, ...]) -> None:
    lines.extend(["## Announced / Date TBD", ""])
    if not rows:
        lines.extend(["No tracked announced events without dates.", ""])
        return

    append_markdown_table(
        lines,
        ("Event", "Known Scope", "Location", "Source", "Last Checked"),
        (
            (
                markdown_link(row.title, row.url),
                escape_markdown_table(row.scope or "TBD"),
                undated_location_cell(row.location),
                markdown_link("Source", row.source_url or row.url),
                format_last_checked(row.last_checked),
            )
            for row in sorted(rows, key=lambda item: item.title.lower())
        ),
    )
    lines.append("")


def append_undated_event_html_section(lines: list[str], rows: tuple[UndatedEvent, ...]) -> None:
    lines.extend(['    <h2 id="announced-date-tbd">Announced / Date TBD</h2>'])
    if not rows:
        lines.append("    <p>No tracked announced events without dates.</p>")
        return

    append_html_table(
        lines,
        ("Event", "Known Scope", "Location", "Source", "Last Checked"),
        (
            (
                html_link(row.title, row.url),
                escape_html(row.scope or "TBD"),
                undated_location_html_cell(row.location),
                html_link("Source", row.source_url or row.url),
                escape_html(format_last_checked(row.last_checked)),
            )
            for row in sorted(rows, key=lambda item: item.title.lower())
        ),
    )


def conference_markdown_links(output_dir: Path, feeds: list[Feed]) -> list[tuple[str, str]]:
    links = [("All Events", "events/all.html")]
    for feed in feeds:
        relative = feed.path.relative_to(output_dir)
        if not relative.parts or relative.parts[0] not in {"series", "category", "event-type", "domain", "group"}:
            continue
        links.append((feed.name, Path("events", relative).with_suffix(".html").as_posix()))
    return links


def clean_stale_event_docs(site_root: Path, expected_paths: set[Path]) -> int:
    markdown_root = site_root / "events"
    if not markdown_root.exists():
        return 0

    cleaned = 0
    for pattern in ("*.md", "*.html"):
        for path in markdown_root.rglob(pattern):
            if path.resolve() in expected_paths:
                continue
            path.unlink()
            cleaned += 1
    return cleaned


def remove_legacy_conference_markdown(site_root: Path) -> None:
    legacy_path = site_root / "conferences.md"
    if legacy_path.exists():
        legacy_path.unlink()


def append_markdown_table(lines: list[str], headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")


def append_html_table(lines: list[str], headers: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> None:
    lines.extend(["    <table>", "      <thead>", "        <tr>"])
    for header in headers:
        lines.append(f"          <th>{escape_html(header)}</th>")
    lines.extend(["        </tr>", "      </thead>", "      <tbody>"])
    for row in rows:
        lines.append("        <tr>")
        for cell in row:
            lines.append(f"          <td>{cell}</td>")
        lines.append("        </tr>")
    lines.extend(["      </tbody>", "    </table>"])


def _partition_rows(
    rows: tuple[EventRow, ...],
    reference_date: date,
) -> tuple[tuple[EventRow, ...], tuple[EventRow, ...]]:
    upcoming = tuple(row for row in rows if row.end_exclusive > reference_date)
    past = tuple(row for row in rows if row.end_exclusive <= reference_date)
    return upcoming, past


def _rows_by_year(
    rows: tuple[EventRow, ...],
    *,
    reverse_years: bool,
) -> tuple[tuple[int, tuple[EventRow, ...]], ...]:
    grouped: dict[int, list[EventRow]] = {}
    for row in rows:
        grouped.setdefault(row.start.year, []).append(row)

    sorted_years = sorted(grouped, reverse=reverse_years)
    return tuple(
        (
            year,
            tuple(sorted(grouped[year], key=lambda row: (row.start, row.title.lower()))),
        )
        for year in sorted_years
    )
