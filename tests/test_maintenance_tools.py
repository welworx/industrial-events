from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from datetime import date as Date
from pathlib import Path

import yaml

from industrial_events.builder import build_site
from industrial_events.cli import build_site as build_site_tool
from industrial_events.cli import list_candidates as candidates_tool
from industrial_events.cli import update_event as update_event_tool
from industrial_events.config import BuildConfig, config_with_overrides, load_build_config
from industrial_events.dataset import event_dataset_to_json, load_event_dataset, read_event_dataset
from industrial_events.link_checks import recent_event_link_targets, yaml_link_targets
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
)
from industrial_events.validation import CalendarBuildError

ROOT = Path(__file__).resolve().parents[1]

FIXTURES = ROOT / "tests" / "fixtures"
TEST_UPDATED_AT = datetime(2026, 5, 4, tzinfo=UTC)


def build_test_config(
    source: Path = FIXTURES / "valid-events",
    output: Path | None = None,
    readme: Path | None = None,
    sources: Path | None = None,
) -> BuildConfig:
    output_dir = output or ROOT / "tests" / ".generated-site" / "calendars"
    readme_path = readme or ROOT / "tests" / ".generated-site" / "README.md"
    sources_dir = sources or ROOT / "sources"
    return config_with_overrides(
        load_build_config(),
        source_dir=source,
        output_dir=output_dir,
        readme_path=readme_path,
        sources_dir=sources_dir,
    )


def generated_readme_template() -> str:
    markers = (
        (README_UPCOMING_START, README_UPCOMING_END),
        (README_SUBMISSION_START, README_SUBMISSION_END),
        (README_SERIES_START, README_SERIES_END),
        (README_ONE_TIME_START, README_ONE_TIME_END),
        (README_SINGLE_EVENT_START, README_SINGLE_EVENT_END),
        (README_SOURCES_START, README_SOURCES_END),
    )
    parts = ["# Temporary README"]
    for start, end in markers:
        parts.extend(["", start, end])
    return "\n".join(parts) + "\n"


def write_demo_event(path: Path) -> str:
    original = "\n".join(
        ["name: Demo Event", "event_types:", "  - conference", 'start: "2026-01-01"', 'end: "2026-01-02"', ""]
    )
    path.write_text(original, encoding="utf-8")
    return original


def run_update_event(args: list[str]) -> tuple[int, str]:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = update_event_tool.main(args)
    return exit_code, stderr.getvalue()


class MaintenanceToolTests(unittest.TestCase):
    def test_recent_event_link_targets_skip_old_event_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "events"
            series_dir = source / "metallurgy" / "demo-series"
            series_dir.mkdir(parents=True)
            (series_dir / "metadata.yaml").write_text(
                "\n".join(
                    [
                        "series: Demo Series",
                        "slug: demo-series",
                        "website: https://example.org/series",
                        "description: Demo series",
                        "categories:",
                        "  - metallurgy",
                        "sources:",
                        "  - type: source",
                        "    url: https://example.org/source",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (series_dir / "demo-series-2024.yaml").write_text(
                "\n".join(
                    [
                        "name: Demo 2024",
                        "event_types:",
                        "  - conference",
                        'start: "2024-05-01"',
                        'end: "2024-05-02"',
                        "url: https://example.org/old",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (series_dir / "demo-series-2025.yaml").write_text(
                "\n".join(
                    [
                        "name: Demo 2025",
                        "event_types:",
                        "  - conference",
                        'start: "2025-05-01"',
                        'end: "2025-05-02"',
                        "url: https://example.org/recent",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (series_dir / "demo-series-2020.yaml").write_text(
                "\n".join(
                    [
                        "name: Demo Undated 2020",
                        "event_types:",
                        "  - conference",
                        "status: estimated",
                        "url: https://example.org/undated-old",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (series_dir / "demo-series-2027.yaml").write_text(
                "\n".join(
                    [
                        "name: Demo Undated 2027",
                        "event_types:",
                        "  - conference",
                        "status: tentative",
                        "url: https://example.org/undated-recent",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            urls = {
                target.url
                for target in recent_event_link_targets(source, reference_date=Date(2026, 5, 11), years_back=1)
            }
            all_urls = {
                target.url
                for target in recent_event_link_targets(source, reference_date=Date(2026, 5, 11), years_back=None)
            }

        self.assertIn("https://example.org/recent", urls)
        self.assertIn("https://example.org/undated-recent", urls)
        self.assertIn("https://example.org/series", urls)
        self.assertIn("https://example.org/source", urls)
        self.assertNotIn("https://example.org/old", urls)
        self.assertNotIn("https://example.org/undated-old", urls)
        self.assertIn("https://example.org/old", all_urls)
        self.assertIn("https://example.org/undated-old", all_urls)

    def test_yaml_link_targets_extract_nested_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "event.yaml"
            yaml_path.write_text(
                "\n".join(
                    [
                        "url: https://example.org/event",
                        "sources:",
                        "  - type: event-site",
                        "    url: https://example.org/source",
                        "note: not a url",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            urls = {target.url for target in yaml_link_targets(yaml_path)}

        self.assertEqual(urls, {"https://example.org/event", "https://example.org/source"})

    def test_yaml_link_targets_skip_inactive_top_level_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            yaml_path = Path(tmp_dir) / "event.yaml"
            yaml_path.write_text(
                "\n".join(
                    [
                        "url: https://example.org/dead-official",
                        "url_status: inactive",
                        "sources:",
                        "  - type: overview",
                        "    url: https://example.org/archive",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            urls = {target.url for target in yaml_link_targets(yaml_path)}

        self.assertEqual(urls, {"https://example.org/archive"})

    def test_event_top_level_urls_do_not_duplicate_evidence_sources(self) -> None:
        evidence_types = {"overview", "event-listing", "proceedings", "reference", "event-report"}
        offenders = []
        for event_path in (ROOT / "events").rglob("*.yaml"):
            if event_path.name == "metadata.yaml":
                continue
            event = yaml.safe_load(event_path.read_text(encoding="utf-8"))
            url = event.get("url")
            if not url or event.get("url_status"):
                continue
            for source in event.get("sources", []) or []:
                if source.get("url") == url and source.get("type") in evidence_types:
                    offenders.append(event_path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, [])

    def assert_update_event_rejected(self, args: list[str], message: str) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            event_path = Path(tmp_dir) / "demo.yaml"
            original = write_demo_event(event_path)

            exit_code, stderr = run_update_event([str(event_path), *args])

            self.assertEqual(exit_code, 1)
            self.assertIn(message, stderr)
            self.assertEqual(event_path.read_text(encoding="utf-8"), original)

    def test_build_site_uses_dataset_when_source_dir_is_missing(self) -> None:
        dataset = load_event_dataset(build_test_config())

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            site_root = tmp_root / "site"
            output_dir = site_root / "calendars"
            readme_path = site_root / "README.md"
            readme_path.parent.mkdir(parents=True, exist_ok=True)
            readme_path.write_text(generated_readme_template(), encoding="utf-8")

            config = build_test_config(
                source=tmp_root / "missing-events",
                output=output_dir,
                readme=readme_path,
                sources=tmp_root / "missing-sources",
            )

            feeds = build_site(
                config,
                updated_at=TEST_UPDATED_AT,
                reference_date=Date(2026, 1, 1),
                dataset=dataset,
            )

            self.assertEqual(len(feeds), 8)
            self.assertTrue((output_dir / "all.ics").exists())
            self.assertTrue((site_root / "events" / "all.html").exists())
            self.assertIn("Demo Conference 2027", readme_path.read_text(encoding="utf-8"))

    def test_site_main_returns_error_for_invalid_dataset_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dataset_path = Path(tmp_dir) / "broken.json"
            dataset_path.write_text("{broken", encoding="utf-8")

            exit_code = build_site_tool.main(["--dataset", str(dataset_path), "--readme-only", "--log-level", "ERROR"])

        self.assertEqual(exit_code, 1)

    def test_load_build_config_supports_site_url_env_override(self) -> None:
        previous = os.environ.get("INDUSTRIAL_EVENTS_SITE_URL")
        os.environ["INDUSTRIAL_EVENTS_SITE_URL"] = "https://events.example.com"
        try:
            config = load_build_config()
        finally:
            if previous is None:
                os.environ.pop("INDUSTRIAL_EVENTS_SITE_URL", None)
            else:
                os.environ["INDUSTRIAL_EVENTS_SITE_URL"] = previous

        self.assertEqual(config.site_url, "https://events.example.com/")

    def test_read_event_dataset_raises_calendar_build_error_for_invalid_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dataset_path = Path(tmp_dir) / "broken.json"
            dataset_path.write_text('{"items":"oops"}', encoding="utf-8")

            with self.assertRaisesRegex(CalendarBuildError, "invalid event dataset artifact"):
                read_event_dataset(dataset_path)

    def test_read_event_dataset_rejects_non_string_tuple_items(self) -> None:
        dataset = load_event_dataset(build_test_config())
        payload = event_dataset_to_json(dataset)
        payload["items"][0]["categories"] = [1]

        with tempfile.TemporaryDirectory() as tmp_dir:
            dataset_path = Path(tmp_dir) / "broken.json"
            dataset_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                CalendarBuildError,
                "CalendarItem.categories must contain only strings",
            ):
                read_event_dataset(dataset_path)

    def test_read_event_dataset_rejects_unsafe_slug_values(self) -> None:
        dataset = load_event_dataset(build_test_config())
        payload = event_dataset_to_json(dataset)
        payload["items"][0]["categories"] = ["../../../../escape"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            dataset_path = Path(tmp_dir) / "broken.json"
            dataset_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                CalendarBuildError,
                "CalendarItem.categories must contain only lowercase slugs",
            ):
                read_event_dataset(dataset_path)

    def test_update_event_rejects_invalid_source_type(self) -> None:
        self.assert_update_event_rejected(
            ["--source-url", "https://example.org/event", "--source-type", "Bad Type"],
            "must be a lowercase slug",
        )

    def test_update_event_rejects_invalid_coordinates(self) -> None:
        self.assert_update_event_rejected(["--latitude", "999", "--longitude", "0"], "must be between -90 and 90")

    def test_update_event_rejects_unsafe_urls(self) -> None:
        self.assert_update_event_rejected(["--url", "javascript:alert(1)"], "must be an http(s) URL")

    def test_update_event_updates_deadline_source_last_checked_without_event_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            event_path = Path(tmp_dir) / "demo.yaml"
            event_path.write_text(
                "\n".join(
                    [
                        "name: Demo Event",
                        "event_types:",
                        "  - conference",
                        'start: "2026-01-01"',
                        'end: "2026-01-02"',
                        "deadlines:",
                        "  - type: papers",
                        '    date: "2025-12-01"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            exit_code = update_event_tool.main(
                [
                    str(event_path),
                    "--deadline-type",
                    "papers",
                    "--deadline-source-url",
                    "https://example.org/cfp",
                    "--last-checked",
                    "2025-10-01",
                ]
            )

            self.assertEqual(exit_code, 0)
            updated = yaml.safe_load(event_path.read_text(encoding="utf-8"))
            self.assertEqual(
                updated["deadlines"][0]["sources"],
                [
                    {
                        "type": "cfp",
                        "scope": "deadline",
                        "url": "https://example.org/cfp",
                        "last_checked": "2025-10-01",
                    }
                ],
            )
            self.assertNotIn("sources", updated)

    def test_update_event_deadline_history_keeps_old_url_when_date_and_url_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            event_path = Path(tmp_dir) / "demo.yaml"
            event_path.write_text(
                "\n".join(
                    [
                        "name: Demo Event",
                        "event_types:",
                        "  - conference",
                        'start: "2026-01-01"',
                        'end: "2026-01-02"',
                        "deadlines:",
                        "  - type: papers",
                        '    date: "2025-12-01"',
                        "    url: https://example.org/old-cfp",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            exit_code = update_event_tool.main(
                [
                    str(event_path),
                    "--deadline-type",
                    "papers",
                    "--deadline-date",
                    "2025-12-15",
                    "--deadline-url",
                    "https://example.org/new-cfp",
                ]
            )

            self.assertEqual(exit_code, 0)
            updated = yaml.safe_load(event_path.read_text(encoding="utf-8"))
            deadline = updated["deadlines"][0]
            self.assertEqual(deadline["date"], "2025-12-15")
            self.assertEqual(deadline["url"], "https://example.org/new-cfp")
            self.assertEqual(
                deadline["history"],
                [
                    {
                        "date": "2025-12-01",
                        "note": "previous value before automated update",
                        "url": "https://example.org/old-cfp",
                    }
                ],
            )

    def test_update_event_supports_absolute_paths_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            event_path = Path(tmp_dir) / "demo.yaml"
            event_path.write_text(
                "\n".join(
                    [
                        "name: Demo Event",
                        "event_types:",
                        "  - conference",
                        'start: "2026-01-01"',
                        'end: "2026-01-02"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = update_event_tool.main([str(event_path), "--name", "Updated Demo Event", "--dry-run"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["event_file"], event_path.as_posix())

    def test_update_event_rejects_invalid_deadline_type(self) -> None:
        self.assert_update_event_rejected(
            ["--deadline-type", "Bad Type", "--deadline-date", "2026-02-01"],
            "must be a lowercase slug",
        )

    def test_update_candidates_rejects_malformed_deadlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_root = Path(tmp_dir) / "events"
            series_dir = source_root / "software" / "demo-conf"
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
                        "deadlines:",
                        "  - type: papers",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = candidates_tool.main(["--source", str(source_root)])

            self.assertEqual(exit_code, 1)
            self.assertIn("field 'date' is required", stderr.getvalue())

    def test_update_candidates_supports_source_dirs_outside_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_root = Path(tmp_dir) / "events"
            series_dir = source_root / "software" / "demo-conf"
            series_dir.mkdir(parents=True)
            event_path = series_dir / "demo-conf-2027.yaml"
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
            event_path.write_text(
                "\n".join(
                    [
                        "name: Demo Conference 2027",
                        "event_types:",
                        "  - conference",
                        'start: "2027-03-10"',
                        'end: "2027-03-12"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = candidates_tool.main(["--source", str(source_root), "--reference-date", "2026-01-01"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["candidates"][0]["event_file"], event_path.as_posix())


if __name__ == "__main__":
    unittest.main()
