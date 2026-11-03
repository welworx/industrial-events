from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

from industrial_events.config import config_with_overrides, load_build_config
from industrial_events.data import load_items
from industrial_events.dataset import event_dataset_from_json, event_dataset_to_json, load_event_dataset
from industrial_events.link_checks import recent_event_link_targets
from industrial_events.validation import CalendarBuildError

ROOT = Path(__file__).resolve().parents[1]

METADATA = {
    "series": "Demo Conference",
    "slug": "demo-conf",
    "description": "Demo series.",
    "recurrence": "recurring",
    "categories": ["software"],
}
EVENT = {
    "name": "Demo Conference 2027",
    "event_types": ["conference"],
    "start": "2027-03-10",
    "end": "2027-03-12",
    "url": "https://example.org/demo-2027",
}


def valid_program() -> dict:
    return {
        "series": "demo-conf",
        "edition": "demo-conf-2027",
        "timezone": "Europe/Lisbon",
        "sources": [
            {
                "type": "program",
                "scope": "event",
                "url": "https://example.org/program.pdf",
                "last_checked": "2027-01-05",
            }
        ],
        "sessions": [
            {
                "id": "2027-03-10-room-a-opening",
                "title": "Opening",
                "type": "oral",
                "date": "2027-03-10",
                "start": "09:00",
                "end": "10:00",
                "room": "Room A",
                "chairs": [{"name": "Ada Chair", "affiliation": "Univ. X"}],
                "talks": [
                    {
                        "paper_id": "P1",
                        "title": "First talk",
                        "start": "09:00",
                        "end": "09:30",
                        "authors": [{"name": "Ann Author", "presenting": True, "affiliations": ["Univ. X"]}],
                        "abstract": "Full abstract text.",
                    },
                    {
                        "paper_id": "P2",
                        "title": "Second talk",
                        "start": "09:30",
                        "end": "10:00",
                        "authors": [{"name": "Bo Author"}],
                    },
                ],
            }
        ],
    }


def load_with_program(
    program: dict | None, *, program_name: str = "demo-conf-2027.program.yaml", event: dict | None = None
):
    with tempfile.TemporaryDirectory() as tmp:
        series = Path(tmp) / "software" / "demo-conf"
        series.mkdir(parents=True)
        (series / "metadata.yaml").write_text(yaml.safe_dump(METADATA))
        (series / "demo-conf-2027.yaml").write_text(yaml.safe_dump(event or EVENT))
        if program is not None:
            (series / program_name).write_text(yaml.safe_dump(program))
        return load_items(Path(tmp))


class ProgramValidationTests(unittest.TestCase):
    def assert_rejected(self, mutate, message: str) -> None:
        program = valid_program()
        mutate(program)
        with self.assertRaises(CalendarBuildError) as ctx:
            load_with_program(program)
        self.assertIn(message, str(ctx.exception))

    def test_valid_program_outside_the_repository_has_no_repository_url(self) -> None:
        self.assertEqual(load_with_program(valid_program())[0].program_data_url, "")

    def test_event_without_program_has_no_program_data_url(self) -> None:
        self.assertEqual(load_with_program(None)[0].program_data_url, "")

    def test_program_file_is_not_loaded_as_an_event(self) -> None:
        self.assertEqual(len(load_with_program(valid_program())), 1)

    def test_orphan_program_file_is_rejected(self) -> None:
        with self.assertRaisesRegex(CalendarBuildError, "no matching event file"):
            load_with_program(valid_program(), program_name="demo-conf-2099.program.yaml")

    def test_unknown_fields_are_rejected(self) -> None:
        self.assert_rejected(lambda p: p.update(bogus=1), "unknown top level field(s): bogus")
        self.assert_rejected(lambda p: p["sessions"][0]["talks"][0].update(bogus=1), "unknown")

    def test_series_and_edition_must_match(self) -> None:
        self.assert_rejected(lambda p: p.update(series="other"), "'series' must be 'demo-conf'")
        self.assert_rejected(lambda p: p.update(edition="demo-conf-2099"), "'edition' must match the file name")

    def test_original_sources_are_required(self) -> None:
        self.assert_rejected(lambda p: p.update(sources=[]), "must list the original program source")

    def test_sessions_are_required(self) -> None:
        self.assert_rejected(lambda p: p.update(sessions=[]), "'sessions' must be a non-empty list")

    def test_session_date_must_be_within_event(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0].update(date="2027-04-01"), "outside the event")

    def test_session_type_must_be_known(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0].update(type="lunch"), "'type' must be one of")

    def test_times_must_be_quoted_hh_mm_strings(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0].update(start="9:00"), "quoted HH:MM")
        self.assert_rejected(lambda p: p["sessions"][0].update(start=540), "quoted HH:MM")

    def test_session_end_must_follow_start(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0].update(end="08:00"), "end must be after start")

    def test_duplicate_session_ids_are_rejected(self) -> None:
        self.assert_rejected(lambda p: p["sessions"].append(p["sessions"][0]), "duplicate session id")

    def test_duplicate_paper_ids_are_rejected(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0]["talks"][1].update(paper_id="P1"), "duplicate paper_id")

    def test_paper_id_must_be_a_string(self) -> None:
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][0].update(paper_id=123), "'paper_id' must be a non-empty"
        )

    def test_talk_must_fit_session_window_and_not_overlap(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0]["talks"][1].update(end="10:30"), "outside its session")
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][1].update(start="09:15"), "before the previous talk ends"
        )

    def test_talk_start_and_end_go_together(self) -> None:
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][0].pop("end"), "start and end must be provided together"
        )

    def test_authors_are_required_and_checked(self) -> None:
        self.assert_rejected(lambda p: p["sessions"][0]["talks"][0].update(authors=[]), "'authors' must be a non-empty")
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][0]["authors"][0].update(presenting="yes"), "'presenting' must be true"
        )

    def test_abstract_url_must_be_http(self) -> None:
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][0].update(abstract_url="javascript:alert(1)"), "must be an http(s) URL"
        )

    def test_program_for_undated_event_is_rejected(self) -> None:
        event = {key: value for key, value in EVENT.items() if key not in {"start", "end"}} | {"status": "estimated"}
        with self.assertRaisesRegex(CalendarBuildError, "program requires a dated event"):
            load_with_program(valid_program(), event=event)

    def test_timezone_is_required(self) -> None:
        self.assert_rejected(lambda p: p.pop("timezone"), "field 'timezone' is required")

    def test_timezone_must_look_like_an_iana_name(self) -> None:
        self.assert_rejected(lambda p: p.update(timezone="Not a zone"), "IANA name")

    def test_sources_require_type_and_last_checked(self) -> None:
        self.assert_rejected(lambda p: p["sources"][0].pop("type"), "'type' is required")
        self.assert_rejected(lambda p: p["sources"][0].pop("last_checked", None), "'last_checked' is required")

    def test_null_lists_are_treated_as_empty(self) -> None:
        program = valid_program()
        program["sessions"][0]["chairs"] = None
        program["sessions"][0]["talks"][0]["keywords"] = None
        program["sessions"][0]["talks"][1]["authors"][0]["affiliations"] = None
        load_with_program(program)

    def test_paper_id_with_surrounding_spaces_is_rejected(self) -> None:
        self.assert_rejected(
            lambda p: p["sessions"][0]["talks"][1].update(paper_id="P2 "), "leading or trailing spaces"
        )

    def test_two_sessions_in_one_room_at_the_same_time_are_rejected(self) -> None:
        def add_clash(program: dict) -> None:
            clash = {**program["sessions"][0], "id": "clash", "start": "09:30", "end": "10:30", "talks": []}
            program["sessions"].append(clash)

        self.assert_rejected(add_clash, "overlaps")

    def test_back_to_back_sessions_in_one_room_are_allowed(self) -> None:
        program = valid_program()
        program["sessions"].append(
            {**program["sessions"][0], "id": "next", "start": "10:00", "end": "11:00", "talks": []}
        )
        load_with_program(program)

    def test_session_may_have_no_talks(self) -> None:
        program = valid_program()
        program["sessions"][0]["talks"] = []
        load_with_program(program)

    def test_repository_programs_are_valid_and_linked(self) -> None:
        items = load_items(ROOT / "events")
        linked = [item for item in items if item.program_data_url]
        self.assertTrue(any(item.series_slug == "lead-zinc" and item.start == date(2026, 11, 1) for item in linked))
        for item in linked:
            self.assertTrue(item.program_data_url.endswith(".program.yaml"))
        lead_zinc = next(item for item in linked if item.series_slug == "lead-zinc" and item.start == date(2026, 11, 1))
        self.assertEqual(
            lead_zinc.program_data_url,
            "https://github.com/welworx/industrial-events/blob/main/events/metallurgy/lead-zinc/"
            "lead-zinc-2026.program.yaml",
        )

    def test_program_urls_are_included_in_recurring_link_checks(self) -> None:
        targets = recent_event_link_targets(ROOT / "events", reference_date=date(2026, 11, 3), years_back=1)
        self.assertTrue(any(target.path.name == "lead-zinc-2026.program.yaml" for target in targets))

    def test_dataset_artifacts_written_before_program_data_url_still_load(self) -> None:
        dataset = load_event_dataset(config_with_overrides(load_build_config(), source_dir=ROOT / "events"))
        payload = event_dataset_to_json(dataset)
        for item in payload["items"]:
            del item["program_data_url"]
        restored = event_dataset_from_json(payload)
        self.assertEqual({item.program_data_url for item in restored.items}, {""})


if __name__ == "__main__":
    unittest.main()
