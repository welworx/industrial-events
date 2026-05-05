from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from datetime import date as Date
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_calendars  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TEST_SITE_ROOT = ROOT / "tests" / ".generated-site"
TEST_OUTPUT = TEST_SITE_ROOT / "calendars"
TEST_UPDATED_AT = datetime(2026, 5, 4, tzinfo=UTC)


def clean_test_output() -> None:
    for path in TEST_SITE_ROOT.rglob("*"):
        if path.is_file() and (
            path.name in {"events.xml", "index.html", "index.json"} or path.suffix in {".ics", ".md"}
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


class BuildCalendarTests(unittest.TestCase):
    def tearDown(self) -> None:
        clean_test_output()

    def test_builds_filter_feeds_from_yaml_sources(self) -> None:
        source = FIXTURES / "valid-conferences"
        output = TEST_OUTPUT

        with self.assertLogs(build_calendars.LOGGER, level="INFO") as logs:
            feeds = build_calendars.build_calendars(
                source,
                output,
                updated_at=TEST_UPDATED_AT,
                reference_date=Date(2026, 1, 1),
            )
        log_output = "\n".join(logs.output)

        self.assertEqual(len(feeds), 7)
        self.assertIn("Building conference outputs", log_output)
        self.assertIn("Found 1 conference source file(s)", log_output)
        self.assertIn("Writing conference Markdown list", log_output)
        self.assertIn("Writing RSS event stream", log_output)
        self.assertTrue((output / "all.ics").exists())
        self.assertTrue((output / "series" / "demo-conf.ics").exists())
        self.assertTrue((output / "category" / "software.ics").exists())
        self.assertTrue((output / "country" / "pt.ics").exists())
        self.assertTrue((output / "domain" / "software.ics").exists())
        self.assertTrue((output / "group" / "demo-events-2027.ics").exists())
        self.assertTrue((output.parent / "conferences.md").exists())
        self.assertTrue((output.parent / "conferences" / "series" / "demo-conf.md").exists())
        self.assertTrue((output.parent / "conferences" / "category" / "software.md").exists())
        self.assertTrue((output.parent / "conferences" / "domain" / "software.md").exists())
        self.assertTrue((output.parent / "conferences" / "group" / "demo-events-2027.md").exists())
        self.assertFalse((output.parent / "conferences" / "country" / "pt.md").exists())
        self.assertTrue((output.parent / "events.xml").exists())
        self.assertTrue((output.parent / "index.html").exists())
        all_calendar = (output / "all.ics").read_text(encoding="utf-8")
        conference_markdown = (output.parent / "conferences.md").read_text(encoding="utf-8")
        series_markdown = (output.parent / "conferences" / "series" / "demo-conf.md").read_text(encoding="utf-8")
        group_markdown = (output.parent / "conferences" / "group" / "demo-events-2027.md").read_text(encoding="utf-8")
        rss_feed = (output.parent / "events.xml").read_text(encoding="utf-8")
        site_index = (output.parent / "index.html").read_text(encoding="utf-8")
        unfolded_calendar = unfold_calendar(all_calendar)
        self.assertIn('href="calendars/all.ics"', site_index)
        self.assertIn('href="conferences.md"', site_index)
        self.assertIn('href="conferences/series/demo-conf.md"', site_index)
        self.assertIn('href="conferences/category/software.md"', site_index)
        self.assertIn('href="conferences/domain/software.md"', site_index)
        self.assertIn('href="conferences/group/demo-events-2027.md"', site_index)
        self.assertNotIn('href="conferences/country/pt.md"', site_index)
        self.assertIn('href="events.xml"', site_index)
        self.assertIn("All Conferences", site_index)
        self.assertIn("# Conferences", conference_markdown)
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
            "| Deadline | Event | Event Dates | Scope / Co-located Conferences | Location | Last Checked |",
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
        readme_opportunities = build_calendars.render_readme_submission_opportunities(
            build_calendars.load_items(source),
            reference_date=Date(2026, 1, 1),
        )
        self.assertIn("## Current Submission Opportunities", readme_opportunities)
        self.assertIn("generated:submission-opportunities:start", readme_opportunities)
        self.assertIn(
            "| [Paper submission: 2026-11-15](<https://example.org/demo-2027>) | "
            "[Demo Conference 2027](<https://example.org/demo-2027>) | "
            "2027-03-10 to 2027-03-12 | TBD | 2026-10-20 |",
            readme_opportunities,
        )
        self.assertIn("# Conference Series: demo-conf", series_markdown)
        self.assertIn("Demo Conference 2028", series_markdown)
        self.assertIn("# Co-located Group: demo-events-2027", group_markdown)
        self.assertIn("Demo Conference 2027", group_markdown)
        self.assertNotIn("Demo Conference 2028", group_markdown)
        self.assertNotIn("Paper submission deadline", conference_markdown)
        ElementTree.fromstring(rss_feed)
        self.assertIn('<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', rss_feed)
        self.assertIn("<title>Conference Events</title>", rss_feed)
        self.assertIn("<pubDate>Mon, 04 May 2026 00:00:00 GMT</pubDate>", rss_feed)
        self.assertIn("<lastBuildDate>Mon, 04 May 2026 00:00:00 GMT</lastBuildDate>", rss_feed)
        self.assertEqual(rss_feed.count("<pubDate>"), 1)
        self.assertIn("<title>Demo Conference 2027</title>", rss_feed)
        self.assertIn("<link>https://example.org/demo-2027</link>", rss_feed)
        self.assertIn('<guid isPermaLink="false">demo-conf-2027-03-10-event@conference-calendars</guid>', rss_feed)
        self.assertIn("Date: 2027-03-10 to 2027-03-12", rss_feed)
        self.assertIn("Disclaimer: This calendar makes existing public conference", rss_feed)
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
        self.assertIn("X-CONFERENCE-COLOCATED-GROUP:demo-events-2027", all_calendar)
        self.assertIn("X-CONFERENCE-COLOCATED-SERIES:demo-conf,demo-workshops", all_calendar)
        self.assertNotIn("Demo Conference 2028", all_calendar)
        self.assertIn("SUMMARY:Demo Conference 2029", all_calendar)
        self.assertIn("UID:demo-conf-2029-04-05-event@conference-calendars", all_calendar)
        unknown_location_event = event_block(all_calendar, "UID:demo-conf-2029-04-05-event@conference-calendars")
        self.assertNotIn("LOCATION:", unknown_location_event)
        self.assertNotIn("GEO:", unknown_location_event)
        self.assertNotIn("X-CONFERENCE-COUNTRY:", unknown_location_event)
        self.assertIn("SUMMARY:Demo Conference: Paper submission deadline", all_calendar)
        self.assertIn("DTSTART;VALUE=DATE:20261115", all_calendar)
        self.assertIn("UID:demo-conf-2027-03-10-papers-2026-11-15@conference-calendars", all_calendar)
        self.assertIn("https://e.test/s", unfolded_calendar)
        self.assertIn("https://e.test/e", unfolded_calendar)
        self.assertIn("https://e.test/cfp", unfolded_calendar)
        self.assertIn("https://e.test/original-cfp", unfolded_calendar)
        self.assertIn("https://e.test/extension", unfolded_calendar)
        self.assertIn("Co-located group: Demo Events 2027 (demo-events-2027)", unfolded_calendar)
        self.assertIn("Co-located series: demo-conf\\, demo-workshops", unfolded_calendar)
        self.assertIn("X-WR-CALDESC:This calendar makes existing public conference", unfolded_calendar)
        self.assertIn("Disclaimer: This calendar makes existing public conference", unfolded_calendar)

    def test_rejects_unknown_fields(self) -> None:
        source = FIXTURES / "invalid-unknown-field"

        with self.assertRaisesRegex(build_calendars.CalendarBuildError, "unknown top level field"):
            build_calendars.build_calendars(source, TEST_OUTPUT)

    def test_conference_markdown_collapses_co_located_conferences(self) -> None:
        items = (
            build_calendars.CalendarItem(
                uid="main-2025-event@conference-calendars",
                summary="Main Conference 2025",
                start=Date(2025, 1, 10),
                end_exclusive=Date(2025, 1, 13),
                series="Main Conference",
                series_slug="main",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="conference",
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
            build_calendars.CalendarItem(
                uid="side-2025-event@conference-calendars",
                summary="Side Conference 2025",
                start=Date(2025, 1, 10),
                end_exclusive=Date(2025, 1, 13),
                series="Side Conference",
                series_slug="side",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="conference",
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
            build_calendars.CalendarItem(
                uid="old-2024-event@conference-calendars",
                summary="Old Conference 2024",
                start=Date(2024, 2, 5),
                end_exclusive=Date(2024, 2, 7),
                series="Old Conference",
                series_slug="old",
                domain="software",
                categories=("software",),
                topics=(),
                country="us",
                kind="conference",
                status="CONFIRMED",
                location="Old Venue, US",
                url="https://example.org/old-2024",
            ),
        )

        conference_markdown = build_calendars.render_conference_markdown(items, reference_date=Date(2026, 1, 1))

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
            build_calendars.CalendarItem(
                uid="extraction-2025-event@conference-calendars",
                summary="Extraction 2025 Meeting & Exhibition",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="Extraction Meeting & Exhibition",
                series_slug="extraction",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="conference",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/extraction-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            build_calendars.CalendarItem(
                uid="copper-2025-event@conference-calendars",
                summary="12th International Copper Conference (Copper 2025)",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="International Copper Conference",
                series_slug="copper",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="conference",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/copper-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            build_calendars.CalendarItem(
                uid="nico-2025-event@conference-calendars",
                summary="6th International Symposium on Nickel and Cobalt (Ni-Co 2025)",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="International Symposium on Nickel and Cobalt",
                series_slug="ni-co",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="conference",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/nico-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            build_calendars.CalendarItem(
                uid="cross-cutting-2025-event@conference-calendars",
                summary="Cross-Cutting Symposia at Extraction 2025",
                start=Date(2025, 11, 16),
                end_exclusive=Date(2025, 11, 21),
                series="Cross-Cutting Symposia at Extraction",
                series_slug="cross-cutting",
                domain="metallurgy",
                categories=("metallurgy",),
                topics=(),
                country="us",
                kind="conference",
                status="CONFIRMED",
                location="Shared Venue, US",
                venue="Shared Venue",
                address="1 Shared Way",
                city="Test City",
                url="https://example.org/cross-cutting-2025",
                co_location_group="extraction-2025",
                co_location_series=("extraction", "copper", "ni-co", "cross-cutting"),
            ),
            build_calendars.CalendarItem(
                uid="copper-2025-11-16-poster-submission-2025-06-01@conference-calendars",
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

        conference_markdown = build_calendars.render_conference_markdown(items, reference_date=Date(2025, 1, 1))

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
