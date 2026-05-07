from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from datetime import date as Date
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from industrial_events import site as build_site  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TEST_SITE_ROOT = ROOT / "tests" / ".generated-site"
TEST_OUTPUT = TEST_SITE_ROOT / "calendars"
TEST_UPDATED_AT = datetime(2026, 5, 4, tzinfo=UTC)


def test_config(
    source: Path = FIXTURES / "valid-events",
    output: Path = TEST_OUTPUT,
) -> build_site.BuildConfig:
    return build_site.config_with_overrides(
        build_site.load_build_config(),
        source_dir=source,
        output_dir=output,
        readme_path=TEST_SITE_ROOT / "README.md",
    )


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

        with self.assertLogs(build_site.LOGGER, level="INFO") as logs:
            feeds = build_site.build_site(
                test_config(source, output),
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
            build_site.submission_status_html_cell((), (), Date(2025, 1, 1), Date(2026, 1, 1)),
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
        config = test_config(source, output)
        readme_items = build_site.load_items(source, config)
        readme_opportunities = build_site.render_readme_submission_opportunities(
            readme_items,
            config,
            reference_date=Date(2026, 1, 1),
        )
        readme_upcoming = build_site.render_readme_upcoming_events(
            readme_items,
            config,
            reference_date=Date(2026, 1, 1),
        )
        readme_series = build_site.render_readme_series_overview(
            build_site.load_series_metadata(source),
            readme_items,
            build_site.load_undated_events(source),
            config,
            reference_date=Date(2026, 1, 1),
        )
        self.assertIn(
            "Full list: [All upcoming events](https://welworx.github.io/industrial-events/events/all.html#upcoming-events).",
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
            "(https://welworx.github.io/industrial-events/events/all.html#submission-opportunities).",
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
            "- **[Demo Conference](<https://welworx.github.io/industrial-events/events/series/demo-conf.html>)**",
            readme_series,
        )
        self.assertNotIn("](<https://e.test/s>)", readme_series)
        self.assertNotIn("**Types:**", readme_series)
        self.assertNotIn("**Tags:**", readme_series)
        self.assertIn("**Series:** ![recurrence: recurring]", readme_series)
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

            section = build_site.render_readme_overview_sources(Path(tmp_dir))

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
            build_site.SeriesMetadata(
                path=Path("beta/metadata.yaml"),
                domain="software",
                series="Zeta Events",
                slug="zeta",
                description="Later alphabetically.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
            ),
            build_site.SeriesMetadata(
                path=Path("alpha/metadata.yaml"),
                domain="software",
                series="Alpha Events",
                slug="alpha",
                description="Earlier alphabetically.",
                recurrence="recurring",
                categories=("software",),
                topics=(),
                sources=("https://example.org/discovery",),
            ),
        )

        section = build_site.render_readme_series_overview(
            metadata,
            (),
            (),
            test_config(),
            reference_date=Date(2026, 1, 1),
        )

        self.assertLess(section.index("Alpha Events"), section.index("Zeta Events"))
        self.assertEqual(section.count("![recurrence: recurring]"), 2)
        self.assertNotIn("](<https://example.org/discovery>)", section)
        self.assertNotIn("**Next:**", section)
        self.assertNotIn("next-TBD", section)

    def test_rejects_unknown_fields(self) -> None:
        source = FIXTURES / "invalid-unknown-field"

        with self.assertRaisesRegex(build_site.CalendarBuildError, "unknown top level field"):
            build_site.build_site(test_config(source, TEST_OUTPUT))

    def test_rejects_missing_source_dir(self) -> None:
        source = FIXTURES / "missing-events"

        with self.assertRaisesRegex(build_site.CalendarBuildError, "source directory does not exist"):
            build_site.build_site(test_config(source, TEST_OUTPUT), updated_at=TEST_UPDATED_AT)

    def test_calendar_categories_do_not_force_conference(self) -> None:
        item = build_site.CalendarItem(
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

        calendar = build_site.render_calendar("Exhibitions", (item,), test_config(), TEST_UPDATED_AT)
        block = event_block(unfold_calendar(calendar), "expo-2027@industrial-events")

        self.assertIn("CATEGORIES:event,industrial-expo,de,exhibition,industry", block)
        self.assertNotIn("conference", block)

    def test_conference_markdown_collapses_co_located_conferences(self) -> None:
        items = (
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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

        conference_markdown = build_site.render_event_markdown(items, reference_date=Date(2026, 1, 1))

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

    def test_conference_markdown_adds_short_contained_conference_labels(self) -> None:
        items = (
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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
            build_site.CalendarItem(
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

        conference_markdown = build_site.render_event_markdown(items, reference_date=Date(2025, 1, 1))

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
