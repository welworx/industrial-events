from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
import re

from industrial_events import data
from industrial_events.validation import CalendarBuildError, optional_date, optional_str, optional_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check for duplicate top-level event URLs across conference editions.")
    parser.add_argument("--source", type=Path, default=Path("events"))
    args = parser.parse_args(argv)

    try:
        duplicates = duplicate_event_urls(args.source)
    except (CalendarBuildError, OSError) as exc:
        print(f"ERROR duplicate event URL check failed: {exc}", file=sys.stderr)
        return 1

    for url, records in duplicates:
        print(f"Duplicate event url: {url}", file=sys.stderr)
        for record in records:
            print(
                f"  - {record['path']}: {record['name']} "
                f"(status={record['status'] or 'confirmed'}, url_status={record['url_status'] or 'active'})",
                file=sys.stderr,
            )
    return 1 if duplicates else 0


def duplicate_event_urls(source_dir: Path) -> list[tuple[str, list[dict[str, str]]]]:
    seen: dict[str, list[dict[str, str]]] = defaultdict(list)
    for metadata_path in data.series_metadata_files(source_dir):
        for event_path in data.event_files(metadata_path):
            event = data.load_event_file(event_path)
            status = optional_str(event, "status", event_path, "event").lower()
            if status in {"tentative", "estimated"}:
                continue
            url = optional_url(event_path, event, "url", "event")
            if not url:
                continue
            seen[url].append(
                {
                    "path": event_path.as_posix(),
                    "name": optional_str(event, "name", event_path, "event"),
                    "status": status,
                    "url_status": optional_str(event, "url_status", event_path, "event").lower(),
                    "year": str(optional_date(event_path, event, "start", "event").year),
                }
            )

    duplicates: list[tuple[str, list[dict[str, str]]]] = []
    for url, records in sorted(seen.items()):
        if len(records) < 2:
            continue
        if not is_year_specific_duplicate(url, records):
            continue
        duplicates.append((url, records))
    return duplicates


YEAR_TOKEN_RE = re.compile(r"(?<!\d)((?:19|20)\d{2}|\d{2})(?!\d)")


def is_year_specific_duplicate(url: str, records: list[dict[str, str]]) -> bool:
    tokens = {match.group(1) for match in YEAR_TOKEN_RE.finditer(url)}
    if not tokens:
        return False
    matches = []
    for record in records:
        year = record["year"]
        short_year = year[-2:]
        matches.append(any(token == year or token == short_year for token in tokens))
    return any(matches) and not all(matches)


if __name__ == "__main__":
    raise SystemExit(main())
