from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from datetime import date as Date
from pathlib import Path
from xml.etree import ElementTree

from industrial_events.builder import LOGGER, build_site, write_event_dataset, write_readme
from industrial_events.config import BuildConfig, config_with_overrides, load_build_config
from industrial_events.data import load_items, load_series_metadata, load_undated_events, load_yaml
from industrial_events.dataset import load_event_dataset, read_event_dataset
from industrial_events.event_pages import render_event_markdown
from industrial_events.event_rows import submission_status_html_cell
from industrial_events.feeds import render_calendar
from industrial_events.models import CalendarItem, SeriesMetadata
from industrial_events.readme import (
    README_ONE_TIME_END,
    README_ONE_TIME_START,
    README_SERIES_END,
    README_SERIES_START,
    README_SINGLE_EVENT_END,
    README_SINGLE_EVENT_START,
    README_SOURCES_END,
    README_SOURCES_START,
    README_SUBMISSION_END,
    README_SUBMISSION_START,
    README_UPCOMING_END,
    README_UPCOMING_START,
    render_readme_one_time_events,
    render_readme_overview_sources,
    render_readme_series_overview,
    render_readme_single_event_records,
    render_readme_submission_opportunities,
    render_readme_upcoming_events,
)
from industrial_events.render_utils import html_link, markdown_link
from industrial_events.validation import CalendarBuildError

ROOT = Path(__file__).resolve().parents[1]

FIXTURES = ROOT / "tests" / "fixtures"
TEST_SITE_ROOT = ROOT / "tests" / ".generated-site"
TEST_OUTPUT = TEST_SITE_ROOT / "calendars"
TEST_UPDATED_AT = datetime(2026, 5, 4, tzinfo=UTC)


def build_test_config(
    source: Path = FIXTURES / "valid-events",
    output: Path = TEST_OUTPUT,
) -> BuildConfig:
    return config_with_overrides(
        load_build_config(),
        source_dir=source,
        output_dir=output,
        readme_path=TEST_SITE_ROOT / "README.md",
    )


def write_demo_event_source(tmp_dir: str, event_fields: list[str]) -> Path:
    source = Path(tmp_dir) / "events"
    series_dir = source / "software" / "demo-conf"
    series_dir.mkdir(parents=True)
    (series_dir / "metadata.yaml").write_text(
        "\n".join(
            [
                "series: Demo Conference",
                "slug: demo-conf",
                "description: Demo series",
                "categories:",
                "  - software",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (series_dir / "demo-conf-2027.yaml").write_text(
        "\n".join(
            [
                "name: Demo Conference 2027",
                "event_types:",
                "  - conference",
                'start: "2027-03-10"',
                'end: "2027-03-12"',
                *event_fields,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return source


def clean_test_output() -> None:
    for path in TEST_SITE_ROOT.rglob("*"):
        if path.is_file() and (
            path.name in {"events.xml", "index.html", "index.json"} or path.suffix in {".ics", ".html", ".md"}
        ):
            try:
                path.unlink()
            except PermissionError:
                pass


def unfold_calendar(value: str) -> str:
    return value.replace("\r\n ", "").replace("\n ", "")


def event_block(calendar: str, uid: str) -> str:
    for block in calendar.split("BEGIN:VEVENT"):
        if uid in block:
            return block
    raise AssertionError(f"event with uid {uid!r} not found")


class BuildSiteTests(unittest.TestCase):
    def tearDown(self) -> None:
        clean_test_output()

    def test_builds_filter_feeds_from_yaml_sources(self) -> None:
        source = FIXTURES / "valid-events"
        output = TEST_OUTPUT

        with self.assertLogs(LOGGER, level="INFO") as logs:
            feeds = build_site(
                build_test_config(source, output),
                updated_at=TEST_UPDATED_AT,
                reference_date=Date(2026, 1, 1),
            )
        log_output = "\n".join(logs.output)

        self.assertEqual(len(feeds), 8)
        self.assertIn("Building event outputs", log_output)
        self.assertIn("Found 1 event series metadata file(s)", log_output)
        self.assertIn("Writing event list pages", log_output)
        self.assertIn("Writing RSS event stream", log_output)
        self.assertTrue((output / "all.ics").exists())
        self.assertTrue((output / "series" / "demo-conf.ics").exists())
        self.assertTrue((output / "category" / "software.ics").exists())
        self.assertTrue((output / "event-type" / "conference.ics").exists())
        self.assertTrue((output / "country" / "pt.ics").exists())
        self.assertTrue((output / "domain" / "software.ics").exists())
        self.assertTrue((output / "group" / "demo-events-2027.ics").exists())
        self.assertFalse((output.parent / "conferences.md").exists())
        self.assertTrue((output.parent / "events" / "all.md").exists())
        self.assertTrue((output.parent / "events" / "all.html").exists())
        self.assertTrue((output.parent / "events" / "series" / "demo-conf.md").exists())
        self.assertTrue((output.parent / "events" / "series" / "demo-conf.html").exists())
        self.assertTrue((output.parent / "events" / "category" / "software.md").exists())
        self.assertTrue((output.parent / "events" / "event-type" / "conference.md").exists())
        self.assertTrue((output.parent / "events" / "domain" / "software.md").exists())
        self.assertTrue((output.parent / "events" / "group" / "demo-events-2027.md").exists())
        self.assertFalse((output.parent / "events" / "country" / "pt.md").exists())
        self.assertTrue((output.parent / "events.xml").exists())
        self.assertTrue((output.parent / "index.html").exists())
        all_calendar = (output / "all.ics").read_text(encoding="utf-8")
        conference_markdown = (output.parent / "events" / "all.md").read_text(encoding="utf-8")
        conference_html = (output.parent / "events" / "all.html").read_text(encoding="utf-8")
        series_markdown = (output.parent / "events" / "series" / "demo-conf.md").read_text(encoding="utf-8")
        group_markdown = (output.parent / "events" / "group" / "demo-events-2027.md").read_text(encoding="utf-8")
        rss_feed = (output.parent / "events.xml").read_text(encoding="utf-8")
        site_index = (output.parent / "index.html").read_text(encoding="utf-8")
        unfolded_calendar = unfold_calendar(all_calendar)
        self.assertIn('href="calendars/all.ics"', site_index)
        self.assertIn('href="https://github.com/welworx/industrial-events"', site_index)
        self.assertIn('href="events/all.html"', site_index)
        self.assertNotIn('href="events/all.md"', site_index)
        self.assertNotIn('href="conferences.md"', site_index)
        self.assertIn('href="events/series/demo-conf.html"', site_index)
        self.assertIn('href="events/category/software.html"', site_index)
        self.assertIn('href="events/domain/software.html"', site_index)
        self.assertIn('href="events/group/demo-events-2027.html"', site_index)
        self.assertNotIn('href="events/country/pt.md"', site_index)
        self.assertIn('href="events.xml"', site_index)
        self.assertIn("All Events", site_index)
        self.assertIn("<!doctype html>", conference_html)
        self.assertIn('<h2 id="submission-opportunities">Submission Opportunities</h2>', conference_html)
        self.assertIn("<table>", conference_html)
        self.assertIn(
            '<a href="https://example.org/demo-2027">Demo Conference 2027</a>',
            conference_html,
        )
        self.assertIn('<span class="status-badge status-open">Open</span>', conference_html)
        self.assertIn('<span class="status-badge status-tbd">TBD</span>', conference_html)
        self.assertIn(
            '<span class="status-badge status-closed">Closed</span>',
            submission_status_html_cell((), (), Date(2025, 1, 1), Date(2026, 1, 1)),
        )
        self.assertIn("# Events", conference_markdown)
        self.assertIn("## Submission Opportunities", conference_markdown)
        self.assertIn("## Upcoming Events", conference_markdown)
        self.assertIn("## Announced / Date TBD", conference_markdown)
        self.assertIn("## Past Events", conference_markdown)
        self.assertNotIn("## Open Submission Deadlines", conference_markdown)
        self.assertNotIn("## Chronological", conference_markdown)
        self.assertNotIn("## Planned", conference_markdown)
        self.assertIn("### 2027", conference_markdown)
        self.assertIn("### 2029", conference_markdown)
        submission_index = conference_markdown.index("## Submission Opportunities")
        upcoming_index = conference_markdown.index("## Upcoming Events")
        undated_index = conference_markdown.index("## Announced / Date TBD")
        past_index = conference_markdown.index("## Past Events")
        upcoming_2027_index = conference_markdown.index("### 2027", upcoming_index)
        self.assertLess(submission_index, upcoming_index)
        self.assertLess(upcoming_index, undated_index)
        self.assertLess(undated_index, past_index)
        self.assertLess(upcoming_index, upcoming_2027_index)
        self.assertLess(upcoming_index, conference_markdown.index("### 2029", upcoming_index))
        self.assertEqual(conference_markdown.count("[Demo Conference 2027](<https://example.org/demo-2027>)"), 2)
        self.assertIn(
            "| Deadline | Event | Event Dates | Scope / Co-located Events | Location | Last Checked |",
            conference_markdown,
        )
        self.assertIn("| Dates | Event | Submission Status | Location | Last Checked |", conference_markdown)
        self.assertIn(
            "| [Paper submission: 2026-11-15](<https://example.org/demo-2027>) | "
            "[Demo Conference 2027](<https://example.org/demo-2027>) | "
            "2027-03-10 to 2027-03-12 | TBD | "
            "[Demo Center](<https://www.google.com/maps/search/?api=1&query=38.7222520,-9.1393370>)<br>"
            "[Rua Demo 1, Lisbon, Portugal]"
            "(<https://www.google.com/maps/search/?api=1&query=38.7222520,-9.1393370>) | "
            "2026-10-20 |",
            conference_markdown,
        )
        self.assertIn(
            "| 2027-03-10 to 2027-03-12 | "
            "[Demo Conference 2027](<https://example.org/demo-2027>) | "
            "Open: [Paper submission: 2026-11-15](<https://example.org/demo-2027>) | "
            "[Demo Center](<https://www.google.com/maps/search/?api=1&query=38.7222520,-9.1393370>)<br>"
            "[Rua Demo 1, Lisbon, Portugal]"
            "(<https://www.google.com/maps/search/?api=1&query=38.7222520,-9.1393370>) | "
            "2026-10-20 |",
            conference_markdown,
        )
        self.assertIn(
            "| 2029-04-05 to 2029-04-07 | "
            "[Demo Conference 2029](<https://example.org/demo-2029>) | TBD | TBD | 2026-08-01 |",
            conference_markdown,
        )
        self.assertIn(
            "| [Demo Conference 2028](<https://example.org/demo-2028>) | TBD | TBD | "
            "[Source](<https://e.test/demo-2028>) | 2026-08-01 |",
            conference_markdown,
        )
        config = build_test_config(source, output)
        readme_items = load_items(source, config)
        readme_opportunities = render_readme_submission_opportunities(
            readme_items,
            config,
            reference_date=Date(2026, 1, 1),
        )
        readme_upcoming = render_readme_upcoming_events(
            readme_items,
            config,
            reference_date=Date(2026, 1, 1),
        )
        readme_series = render_readme_series_overview(
            load_series_metadata(source),
            readme_items,
            load_undated_events(source),
            config,
            reference_date=Date(2026, 1, 1),
        )
        self.assertIn(
            "Full list: [All upcoming events](https://industrial-events.pages.dev/events/all.html#upcoming-events).",
            readme_upcoming,
        )
        self.assertIn("### 2027", readme_upcoming)
        self.assertIn("#### March", readme_upcoming)
        self.assertIn("10-12: [Demo Conference 2027]", readme_upcoming)
        self.assertIn("![type: conference](https://img.shields.io/badge/type-conference-blue)", readme_upcoming)
        self.assertIn(
            "[![location: Lisbon, PT]"
            "(https://img.shields.io/badge/location-Lisbon%2C%20PT-informational)]"
            "(<https://www.google.com/maps/search/?api=1&query=38.7222520,-9.1393370>)",
            readme_upcoming,
        )
        self.assertIn(
            "![CFP: due 2026-11-15](https://img.shields.io/badge/CFP-due%202026--11--15-brightgreen)",
            readme_upcoming,
        )
        self.assertNotIn("![CFP: TBD]", readme_upcoming)
        demo_2029_line = next(line for line in readme_upcoming.splitlines() if "Demo Conference 2029" in line)
        self.assertLess(demo_2029_line.index("![location: Location TBD]"), demo_2029_line.index("![venue: TBD]"))
        self.assertLess(demo_2029_line.index("![venue: TBD]"), demo_2029_line.index("![type: conference]"))
        self.assertNotIn("Generated by `uv run python scripts/build_site.py`", readme_upcoming)
        self.assertNotIn("| Dates | Event | Types | Tags | Location |", readme_upcoming)
        self.assertIn("generated:submission-opportunities:start", readme_opportunities)
        self.assertIn(
            "Full list: [All submission opportunities]"
            "(https://industrial-events.pages.dev/events/all.html#submission-opportunities).",
            readme_opportunities,
        )
        self.assertNotIn("Full list: [https://", readme_opportunities)
        self.assertIn(
            "| [Paper submission: 2026-11-15](<https://example.org/demo-2027>) | "
            "[Demo Conference 2027](<https://example.org/demo-2027>) | "
            "Mar 10-12, 2027 | TBD | 2026-10-20 |",
            readme_opportunities,
        )
        self.assertIn(
            "- **[Demo Conference](<https://industrial-events.pages.dev/events/series/demo-conf.html>)**",
            readme_series,
        )
        self.assertNotIn("](<https://e.test/s>)", readme_series)
        self.assertNotIn("**Types:**", readme_series)
        self.assertNotIn("**Tags:**", readme_series)
        self.assertNotIn("**Series:**", readme_series)
        self.assertIn("![frequency: 1 year]", readme_series)
        self.assertNotIn("![status:", readme_series)
        self.assertIn("**Next:** ", readme_series)
        self.assertIn(
            "![next: Mar 10-12, 2027](https://img.shields.io/badge/next-Mar%2010--12%2C%202027-brightgreen) "
            "[Demo Conference 2027]",
            readme_series,
        )
        self.assertNotIn("| Series | Represents | Types | Tags | Next tracked event |", readme_series)
        self.assertNotIn("2027-03-10 to 2027-03-12", readme_series)
        self.assertIn("# Event Series: demo-conf", series_markdown)
        self.assertIn("Demo Conference 2028", series_markdown)
        self.assertIn("# Co-located Group: demo-events-2027", group_markdown)
        self.assertIn("Demo Conference 2027", group_markdown)
        self.assertNotIn("Demo Conference 2028", group_markdown)
        self.assertNotIn("Paper submission deadline", conference_markdown)
        ElementTree.fromstring(rss_feed)
        self.assertIn('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', rss_feed)
        self.assertIn("<title>Event Updates</title>", rss_feed)
        self.assertIn("<pubDate>Mon, 04 May 2026 00:00:00 GMT</pubDate>", rss_feed)
        self.assertIn("<lastBuildDate>Mon, 04 May 2026 00:00:00 GMT</lastBuildDate>", rss_feed)
        self.assertEqual(rss_feed.count("<pubDate>"), 1)
        self.assertIn("<title>Demo Conference 2027</title>", rss_feed)
        self.assertIn("<link>https://example.org/demo-2027</link>", rss_feed)
        self.assertIn('<guid isPermaLink="false">demo-conf-demo-conf-2027@industrial-events</guid>', rss_feed)
        self.assertIn("Date: 2027-03-10 to 2027-03-12", rss_feed)
        self.assertIn("Disclaimer: This calendar makes existing public event", rss_feed)
        self.assertIn("SUMMARY:Demo Conference 2027", all_calendar)
        self.assertIn("X-PUBLISHED-TTL:PT6H", all_calendar)
        self.assertIn("DTSTART;VALUE=DATE:20270310", all_calendar)
        self.assertIn("DTEND;VALUE=DATE:20270313", all_calendar)
        self.assertIn("CREATED:20260504T000000Z", all_calendar)
        self.assertIn("LAST-MODIFIED:20260504T000000Z", all_calendar)
        self.assertIn("SEQUENCE:0", all_calendar)
        self.assertIn("CLASS:PUBLIC", all_calendar)
        self.assertIn("TRANSP:OPAQUE", all_calendar)
        self.assertIn("GEO:38.7222520;-9.1393370", all_calendar)
        self.assertIn("X-EVENT-TYPES:conference", all_calendar)
        self.assertIn("X-EVENT-COLOCATED-GROUP:demo-events-2027", all_calendar)
        self.assertIn("X-EVENT-COLOCATED-SERIES:demo-conf,demo-workshops", all_calendar)
        self.assertNotIn("Demo Conference 2028", all_calendar)
        self.assertIn("SUMMARY:Demo Conference 2029", all_calendar)
        self.assertIn("UID:demo-conf-demo-conf-2029@industrial-events", all_calendar)
        unknown_location_event = event_block(all_calendar, "UID:demo-conf-demo-conf-2029@industrial-events")
        self.assertNotIn("LOCATION:", unknown_location_event)
        self.assertNotIn("GEO:", unknown_location_event)
        self.assertNotIn("X-EVENT-COUNTRY:", unknown_location_event)
        self.assertIn("SUMMARY:Demo Conference: Paper submission deadline", all_calendar)
        self.assertIn("DTSTART;VALUE=DATE:20261115", all_calendar)
        self.assertIn("UID:demo-conf-demo-conf-2027-deadline-1-papers@industrial-events", all_calendar)
        self.assertIn("https://e.test/s", unfolded_calendar)
        self.assertIn("https://e.test/e", unfolded_calendar)
        self.assertIn("https://e.test/cfp", unfolded_calendar)
        self.assertIn("https://e.test/original-cfp", unfolded_calendar)
        self.assertIn("https://e.test/extension", unfolded_calendar)
        self.assertIn("Co-located group: Demo Events 2027 (demo-events-2027)", unfolded_calendar)
        self.assertIn("Co-located series: demo-conf\\, demo-workshops", unfolded_calendar)
        self.assertIn("X-WR-CALDESC:This calendar makes existing public event", unfolded_calendar)
        self.assertIn("Disclaimer: This calendar makes existing public event", unfolded_calendar)

    def test_event_dataset_round_trips_json(self) -> None:
        dataset = load_event_dataset(build_test_config())

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "events-dataset.json"
            write_event_dataset(path, dataset)
            loaded = read_event_dataset(path)

        self.assertEqual(dataset.items, loaded.items)
        self.assertEqual(dataset.undated_events, loaded.undated_events)
        self.assertEqual(dataset.series_metadata, loaded.series_metadata)
        self.assertEqual(dataset.source_pages, loaded.source_pages)

    def test_readme_update_preserves_manual_content_and_rebuilds_all_generated_blocks(self) -> None:
        config = build_test_config()
        dataset = load_event_dataset(config)
        stale_sections = {
            README_UPCOMING_START: README_UPCOMING_END,
            README_SUBMISSION_START: README_SUBMISSION_END,
            README_SERIES_START: README_SERIES_END,
            README_ONE_TIME_START: README_ONE_TIME_END,
            README_SINGLE_EVENT_START: README_SINGLE_EVENT_END,
            README_SOURCES_START: README_SOURCES_END,
        }
        readme_parts = ["# Manual title", "", "Manual intro survives."]
        for index, (start_marker, end_marker) in enumerate(stale_sections.items(), start=1):
            readme_parts.extend(
                [
                    "",
                    f"Manual content before generated block {index}.",
                    start_marker,
                    f"stale generated content {index}",
                    end_marker,
                    f"Manual content after generated block {index}.",
                ]
            )
        config.readme_path.parent.mkdir(parents=True, exist_ok=True)
        config.readme_path.write_text("\n".join(readme_parts) + "\n", encoding="utf-8")

        write_readme(config, dataset, reference_date=Date(2026, 1, 1))

        updated = config.readme_path.read_text(encoding="utf-8")
        self.assertIn("# Manual title", updated)
        self.assertIn("Manual intro survives.", updated)
        for index in range(1, len(stale_sections) + 1):
            self.assertIn(f"Manual content before generated block {index}.", updated)
            self.assertIn(f"Manual content after generated block {index}.", updated)
            self.assertNotIn(f"stale generated content {index}", updated)
        self.assertIn("Demo Conference 2027", updated)
        self.assertIn("Paper submission: 2026-11-15", updated)
        self.assertIn("Demo Conference", updated)
        self.assertIn("No one-time events are tracked separately right now.", updated)
        self.assertIn("No single-event records are tracked separately right now.", updated)
        self.assertIn("Discovery sources help find and monitor events.", updated)

    def test_readme_overview_sources_do_not_show_global_last_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "metallurgy" / "overview.yaml"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "\n".join(
                    [
                        "name: Example Overview",
                        "slug: example-overview",
                        "url: https://example.org/events",
                        "type: overview",
                        "categories:",
                        "  - metallurgy",
                        "topics:",
                        "  - pyrometallurgy",
                        'last_checked: "2026-05-05"',
                    ]
                ),
                encoding="utf-8",
            )

            section = render_readme_overview_sources(Path(tmp_dir))

        self.assertIn("Discovery sources help find and monitor events.", section)
        self.assertIn("- [Example Overview](<https://example.org/events>)", section)
        self.assertNotIn("| Source | Type | Tags |", section)
        self.assertNotIn("`metallurgy`", section)
        self.assertNotIn("overview |", section)
        self.assertNotIn("Generated by `uv run python scripts/build_site.py`", section)
        self.assertNotIn("Last Checked", section)
        self.assertNotIn("2026-05-05", section)

    def test_readme_series_overview_sorts_by_series_name(self) -> None:
        metadata = (
            SeriesMetadata(
                path=Path("zeta/metadata.yaml"),
                domain="software",
                series="Zeta Events",
                slug="zeta",
                description="Later alphabetically with two events.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
            ),
            SeriesMetadata(
                path=Path("alpha/metadata.yaml"),
                domain="software",
                series="Alpha Events",
                slug="alpha",
                description="Earlier alphabetically with two events.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
                sources=("https://example.org/discovery",),
            ),
            SeriesMetadata(
                path=Path("one/metadata.yaml"),
                domain="software",
                series="One-Time Summit",
                slug="one-time",
                description="Standalone event.",
                recurrence="one-off",
                categories=("software",),
                topics=(),
            ),
            SeriesMetadata(
                path=Path("single/metadata.yaml"),
                domain="software",
                series="Singleton Conf",
                slug="single-conf",
                description="Only one tracked event so far.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
            ),
            SeriesMetadata(
                path=Path("cadence/metadata.yaml"),
                domain="software",
                series="Cadence Conf",
                slug="cadence-conf",
                description="Two tracked events.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
            ),
        )
        items = (
            CalendarItem(
                uid="alpha-2024@industrial-events",
                summary="Alpha Events 2024",
                start=Date(2024, 4, 1),
                end_exclusive=Date(2024, 4, 2),
                series="Alpha Events",
                series_slug="alpha",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/alpha-2024",
            ),
            CalendarItem(
                uid="alpha-2026@industrial-events",
                summary="Alpha Events 2026",
                start=Date(2026, 4, 1),
                end_exclusive=Date(2026, 4, 2),
                series="Alpha Events",
                series_slug="alpha",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/alpha-2026",
            ),
            CalendarItem(
                uid="zeta-2024@industrial-events",
                summary="Zeta Events 2024",
                start=Date(2024, 3, 1),
                end_exclusive=Date(2024, 3, 2),
                series="Zeta Events",
                series_slug="zeta",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/zeta-2024",
            ),
            CalendarItem(
                uid="zeta-2026@industrial-events",
                summary="Zeta Events 2026",
                start=Date(2026, 3, 1),
                end_exclusive=Date(2026, 3, 2),
                series="Zeta Events",
                series_slug="zeta",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/zeta-2026",
            ),
            CalendarItem(
                uid="one-time-2026@industrial-events",
                summary="One-Time Summit 2026",
                start=Date(2026, 6, 1),
                end_exclusive=Date(2026, 6, 2),
                series="One-Time Summit",
                series_slug="one-time",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/one-time",
            ),
            CalendarItem(
                uid="single-conf-2026@industrial-events",
                summary="Singleton Conf 2026",
                start=Date(2026, 7, 1),
                end_exclusive=Date(2026, 7, 2),
                series="Singleton Conf",
                series_slug="single-conf",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/single",
            ),
            CalendarItem(
                uid="cadence-conf-2024@industrial-events",
                summary="Cadence Conf 2024",
                start=Date(2024, 5, 1),
                end_exclusive=Date(2024, 5, 2),
                series="Cadence Conf",
                series_slug="cadence-conf",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/cadence-2024",
            ),
            CalendarItem(
                uid="cadence-conf-2026@industrial-events",
                summary="Cadence Conf 2026",
                start=Date(2026, 5, 1),
                end_exclusive=Date(2026, 5, 2),
                series="Cadence Conf",
                series_slug="cadence-conf",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/cadence-2026",
            ),
        )

        section = render_readme_series_overview(
            metadata,
            items,
            (),
            build_test_config(),
            reference_date=Date(2026, 1, 1),
        )
        later_section = render_readme_series_overview(
            metadata,
            items,
            (),
            build_test_config(),
            reference_date=Date(2027, 1, 1),
        )
        one_time_section = render_readme_one_time_events(
            metadata,
            items,
            (),
            build_test_config(),
            reference_date=Date(2026, 1, 1),
        )
        single_section = render_readme_single_event_records(
            metadata,
            items,
            (),
            build_test_config(),
            reference_date=Date(2026, 1, 1),
        )

        self.assertLess(section.index("Alpha Events"), section.index("Zeta Events"))
        self.assertGreaterEqual(section.count("![span: 2 years]"), 3)
        self.assertNotIn("One-Time Summit", section)
        self.assertNotIn("Singleton Conf", section)
        self.assertIn("Cadence Conf", section)
        self.assertIn("![span: 2 years]", section)
        self.assertNotIn("](<https://example.org/discovery>)", section)
        self.assertNotIn("next-TBD", section)
        self.assertIn(
            "![next: probably 2028](https://img.shields.io/badge/next-probably%202028-yellow)",
            later_section,
        )
        self.assertNotIn("**Series:**", section)
        self.assertIn("One-Time Summit", one_time_section)
        self.assertIn("![event: one-time]", one_time_section)
        self.assertNotIn("![status:", one_time_section)
        self.assertIn("**Event:**", one_time_section)
        self.assertIn("Singleton Conf", single_section)
        self.assertNotIn("Cadence Conf", single_section)
        self.assertNotIn("![recurrence: recurring]", single_section)
        self.assertIn("**Event:**", single_section)

    def test_rejects_unknown_fields(self) -> None:
        source = FIXTURES / "invalid-unknown-field"

        with self.assertRaisesRegex(CalendarBuildError, "unknown top level field"):
            build_site(build_test_config(source, TEST_OUTPUT))

    def test_rejects_unsafe_event_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = write_demo_event_source(tmp_dir, ["url: javascript:alert(1)"])

            with self.assertRaisesRegex(CalendarBuildError, "must be an http\\(s\\) URL"):
                load_items(source, build_test_config(source))

    def test_rejects_non_string_optional_event_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = write_demo_event_source(tmp_dir, ["status: true"])

            with self.assertRaisesRegex(CalendarBuildError, "status.*must be a string"):
                load_items(source, build_test_config(source))

    def test_rejects_non_string_optional_location_fields_with_event_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = write_demo_event_source(tmp_dir, ["country: true"])

            with self.assertRaisesRegex(
                CalendarBuildError, "demo-conf-2027.yaml.*event field 'country' must be a string"
            ):
                load_items(source, build_test_config(source))

    def test_drops_unsafe_links_during_rendering(self) -> None:
        self.assertEqual(markdown_link("Unsafe", "javascript:alert(1)"), "Unsafe")
        self.assertEqual(html_link("Unsafe", "javascript:alert(1)"), "Unsafe")

    def test_load_yaml_wraps_read_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(CalendarBuildError, "cannot read YAML file"):
                load_yaml(Path(tmp_dir))

    def test_load_yaml_keeps_no_country_code_as_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "event.yaml"
            yaml_path.write_text("country: NO\nstatus: true\n", encoding="utf-8")

            data = load_yaml(yaml_path)

        self.assertEqual(data["country"], "NO")
        self.assertIs(data["status"], True)

    def test_rejects_output_dir_that_is_not_generated_calendars_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            site_root = Path(tmp_dir) / "site"
            output = site_root / "not-calendars"
            stale_page = site_root / "events" / "stale.html"
            stale_page.parent.mkdir(parents=True)
            stale_page.write_text("manual content", encoding="utf-8")

            with self.assertRaisesRegex(CalendarBuildError, "generated 'calendars' directory"):
                build_site(
                    build_test_config(FIXTURES / "valid-events", output),
                    updated_at=TEST_UPDATED_AT,
                    reference_date=Date(2026, 1, 1),
                )

            self.assertEqual(stale_page.read_text(encoding="utf-8"), "manual content")

    def test_rejects_missing_source_dir(self) -> None:
        source = FIXTURES / "missing-events"

        with self.assertRaisesRegex(CalendarBuildError, "source directory does not exist"):
            build_site(build_test_config(source, TEST_OUTPUT), updated_at=TEST_UPDATED_AT)

    def test_calendar_categories_do_not_force_conference(self) -> None:
        item = CalendarItem(
            uid="expo-2027@industrial-events",
            summary="Industrial Expo 2027",
            start=Date(2027, 5, 1),
            end_exclusive=Date(2027, 5, 2),
            series="Industrial Expo",
            series_slug="industrial-expo",
            domain="industry",
            categories=("industry",),
            topics=(),
            country="de",
            kind="event",
            status="CONFIRMED",
            event_types=("exhibition",),
        )

        calendar = render_calendar("Exhibitions", (item,), build_test_config(), TEST_UPDATED_AT)
        block = event_block(unfold_calendar(calendar), "expo-2027@industrial-events")

        self.assertIn("CATEGORIES:event,industrial-expo,de,exhibition,industry", block)
        self.assertNotIn("conference", block)

    def test_conference_markdown_collapses_co_located_conferences(self) -> None:
        items = (
            CalendarItem(
                uid="main-2025-event@industrial-events",
                summary="Main Conference 2025",
                start=Date(2025, 1, 10),
                end_exclusive=Date(2025, 1, 13),
                series="Main Conference",
                series_slug="main",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/main-2025",
                co_location_group="joint-2025",
                co_location_name="Joint Event 2025",
                co_location_url="https://example.org/joint-2025",
                co_location_series=("main", "side"),
            ),
            CalendarItem(
                uid="side-2025-event@industrial-events",
                summary="Side Conference 2025",
                start=Date(2025, 1, 10),
                end_exclusive=Date(2025, 1, 13),
                series="Side Conference",
                series_slug="side",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/side-2025",
                co_location_group="joint-2025",
                co_location_name="Joint Event 2025",
                co_location_url="https://example.org/joint-2025",
                co_location_series=("main", "side"),
            ),
            CalendarItem(
                uid="old-2024-event@industrial-events",
                summary="Old Conference 2024",
                start=Date(2024, 2, 5),
                end_exclusive=Date(2024, 2, 7),
                series="Old Conference",
                series_slug="old",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Old Venue, US",
                url="https://example.org/old-2024",
            ),
        )

        conference_markdown = render_event_markdown(items, reference_date=Date(2026, 1, 1))

        self.assertLess(conference_markdown.index("### 2025"), conference_markdown.index("### 2024"))
        self.assertNotIn("Joint Event 2025", conference_markdown)
        self.assertIn(
            "| 2025-01-10 to 2025-01-12 | "
            "[Main Conference 2025](<https://example.org/main-2025>) "
            "([Side Conference 2025](<https://example.org/side-2025>)) | "
            "Closed | "
            "[Shared Venue](<https://www.google.com/maps/search/?api=1&query=Shared%20Venue,%20US>)<br>"
            "[1 Shared Way]"
            "(<https://www.google.com/maps/search/?api=1&query=Shared%20Venue,%20US>) | "
            "TBD |",
            conference_markdown,
        )

    def test_in_progress_events_remain_upcoming_until_they_end(self) -> None:
        items = (
            CalendarItem(
                uid="ongoing-2026@industrial-events",
                summary="Ongoing Conference 2026",
                start=Date(2026, 1, 1),
                end_exclusive=Date(2026, 1, 4),
                series="Ongoing Conference",
                series_slug="ongoing",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/ongoing",
            ),
        )

        conference_markdown = render_event_markdown(items, reference_date=Date(2026, 1, 2))
        upcoming_section = conference_markdown[
            conference_markdown.index("## Upcoming Events") : conference_markdown.index("## Announced / Date TBD")
        ]
        past_section = conference_markdown[conference_markdown.index("## Past Events") :]
        readme_upcoming = render_readme_upcoming_events(
            items,
            build_test_config(),
            reference_date=Date(2026, 1, 2),
        )

        self.assertIn("Ongoing Conference 2026", upcoming_section)
        self.assertNotIn("Ongoing Conference 2026", past_section)
        self.assertIn("Ongoing Conference 2026", readme_upcoming)

    def test_readme_upcoming_shows_proceedings_and_program_icons_when_available(self) -> None:
        items = (
            CalendarItem(
                uid="demo-2027@industrial-events",
                summary="Demo Conference 2027",
                start=Date(2027, 3, 10),
                end_exclusive=Date(2027, 3, 13),
                series="Demo Conference",
                series_slug="demo-conf",
                domain="software",
                categories=("software",),
                topics=(),
                country="pt",
                kind="event",
                status="CONFIRMED",
                url="https://example.org/demo-2027",
                proceedings_url="https://example.org/demo-2027/proceedings",
                program_url="https://example.org/demo-2027/program",
            ),
        )

        readme_upcoming = render_readme_upcoming_events(
            items,
            build_test_config(),
            reference_date=Date(2026, 1, 1),
        )

        self.assertIn("[📘](<https://example.org/demo-2027/proceedings>)", readme_upcoming)
        self.assertIn("[🗓](<https://example.org/demo-2027/program>)", readme_upcoming)

    def test_conference_markdown_adds_short_contained_conference_labels(self) -> None:
        items = (
            CalendarItem(
                uid="extraction-2025-event@industrial-events",
                summary="Extraction 2025 Meeting & Exhibition",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="Extraction Meeting & Exhibition",
                series_slug="extraction",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/extraction-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            CalendarItem(
                uid="copper-2025-event@industrial-events",
                summary="12th International Copper Conference (Copper 2025)",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="International Copper Conference",
                series_slug="copper",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/copper-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            CalendarItem(
                uid="nico-2025-event@industrial-events",
                summary="6th International Symposium on Nickel and Cobalt (Ni-Co 2025)",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="International Symposium on Nickel and Cobalt",
                series_slug="ni-co",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/nico-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            CalendarItem(
                uid="cross-cutting-2025-event@industrial-events",
                summary="Cross-Cutting Symposia at Extraction 2025",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="Cross-Cutting Symposia at Extraction",
                series_slug="cross-cutting",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="event",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/cross-cutting-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            CalendarItem(
                uid="copper-2025-11-16-poster-submission-2025-06-01@industrial-events",
                summary="International Copper Conference: Poster submission deadline",
                start=Date(2025, 6, 1),
                end_exclusive=Date(2025, 6, 2),
                series="International Copper Conference",
                series_slug="copper",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="deadline-poster-submission",
                status="CONFIRMED",
                url="https://example.org/copper-posters",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
        )

        conference_markdown = render_event_markdown(items, reference_date=Date(2025, 1, 1))

        self.assertIn(
            "| 2025-11-16 to 2025-11-20 | "
            "[Extraction 2025 Meeting & Exhibition](<https://example.org/extraction-2025>) "
            "([Copper 2025](<https://example.org/copper-2025>), "
            "[Ni-Co 2025](<https://example.org/nico-2025>), "
            "[Cross-Cutting 2025](<https://example.org/cross-cutting-2025>)) | "
            "Open: [Copper 2025: Poster submission: 2025-06-01](<https://example.org/copper-posters>) | "
            "[Shared Venue](<https://www.google.com/maps/search/?api=1&query=Shared%20Venue,%20US>)<br>"
            "[1 Shared Way]"
            "(<https://www.google.com/maps/search/?api=1&query=Shared%20Venue,%20US>) | "
            "TBD |",
            conference_markdown,
        )


if __name__ == "__main__":
    unittest.main()
