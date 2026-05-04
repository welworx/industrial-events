from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_calendars  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TEST_OUTPUT = ROOT / "tests" / ".generated-calendars"


def clean_test_output() -> None:
    for path in TEST_OUTPUT.rglob("*"):
        if path.is_file() and (path.name == "index.json" or path.suffix == ".ics"):
            try:
                path.unlink()
            except PermissionError:
                pass


def unfold_calendar(value: str) -> str:
    return value.replace("\r\n ", "").replace("\n ", "")


class BuildCalendarTests(unittest.TestCase):
    def tearDown(self) -> None:
        clean_test_output()

    def test_builds_filter_feeds_from_yaml_sources(self) -> None:
        source = FIXTURES / "valid-conferences"
        output = TEST_OUTPUT

        feeds = build_calendars.build_calendars(source, output)

        self.assertEqual(len(feeds), 7)
        self.assertTrue((output / "all.ics").exists())
        self.assertTrue((output / "series" / "demo-conf.ics").exists())
        self.assertTrue((output / "category" / "software.ics").exists())
        self.assertTrue((output / "country" / "pt.ics").exists())
        self.assertTrue((output / "domain" / "software.ics").exists())
        self.assertTrue((output / "group" / "demo-events-2027.ics").exists())
        all_calendar = (output / "all.ics").read_text(encoding="utf-8")
        unfolded_calendar = unfold_calendar(all_calendar)
        self.assertIn("SUMMARY:Demo Conference 2027", all_calendar)
        self.assertIn("DTSTART;VALUE=DATE:20270310", all_calendar)
        self.assertIn("DTEND;VALUE=DATE:20270313", all_calendar)
        self.assertIn("GEO:38.7222520;-9.1393370", all_calendar)
        self.assertIn("X-CONFERENCE-COLOCATED-GROUP:demo-events-2027", all_calendar)
        self.assertIn("X-CONFERENCE-COLOCATED-SERIES:demo-conf,demo-workshops", all_calendar)
        self.assertNotIn("Demo Conference 2028", all_calendar)
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


if __name__ == "__main__":
    unittest.main()
