from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import build_site

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "events"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List event YAML files that are likely to need verification.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument(
        "--series", action="append", default=[], help="Series slug. Can be repeated or comma-separated."
    )
    parser.add_argument(
        "--category", action="append", default=[], help="Category slug. Can be repeated or comma-separated."
    )
    parser.add_argument(
        "--event-type", action="append", default=[], help="Event type slug. Can be repeated or comma-separated."
    )
    parser.add_argument(
        "--from", dest="date_from", type=parse_date_arg, help="Only include events ending on/after this date."
    )
    parser.add_argument(
        "--to", dest="date_to", type=parse_date_arg, help="Only include events starting on/before this date."
    )
    parser.add_argument("--reference-date", type=parse_date_arg, default=date.today())
    parser.add_argument("--stale-after-days", type=int, default=60)
    parser.add_argument("--past-grace-days", type=int, default=31)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    candidates = list_candidates(
        args.source,
        series=expand_filter(args.series),
        categories=expand_filter(args.category),
        event_types=expand_filter(args.event_type),
        date_from=args.date_from,
        date_to=args.date_to,
        reference_date=args.reference_date,
        stale_after_days=args.stale_after_days,
        past_grace_days=args.past_grace_days,
    )
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "reference_date": args.reference_date.isoformat(),
        "filters": {
            "series": sorted(expand_filter(args.series)),
            "categories": sorted(expand_filter(args.category)),
            "event_types": sorted(expand_filter(args.event_type)),
            "from": args.date_from.isoformat() if args.date_from else None,
            "to": args.date_to.isoformat() if args.date_to else None,
            "stale_after_days": args.stale_after_days,
            "past_grace_days": args.past_grace_days,
        },
        "count": len(candidates),
        "candidates": candidates,
    }
    print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def list_candidates(
    source_dir: Path,
    *,
    series: set[str],
    categories: set[str],
    event_types: set[str],
    date_from: date | None,
    date_to: date | None,
    reference_date: date,
    stale_after_days: int,
    past_grace_days: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    stale_before = reference_date - timedelta(days=stale_after_days)
    past_cutoff = reference_date - timedelta(days=past_grace_days)

    for metadata_path in build_site.series_metadata_files(source_dir):
        metadata = build_site.load_series_metadata_file(source_dir, metadata_path)
        if series and metadata.slug not in series:
            continue
        if categories and not categories.intersection(metadata.categories):
            continue

        for event_path in build_site.event_files(metadata_path):
            event = build_site.load_event_file(event_path)
            current_event_types = set(build_site.require_slug_list(event_path, event, "event_types", "event"))
            if event_types and not event_types.intersection(current_event_types):
                continue

            start = build_site.optional_date(event_path, event, "start", "event")
            end = build_site.optional_date(event_path, event, "end", "event")
            if not date_range_matches(start, end, date_from, date_to):
                continue

            last_checked = latest_checked(metadata, event_path, event)
            reasons = update_reasons(
                event,
                start,
                end,
                last_checked,
                reference_date,
                stale_before,
                past_cutoff,
            )
            if not reasons:
                continue

            candidates.append(
                {
                    "series": metadata.slug,
                    "series_name": metadata.series,
                    "domain": metadata.domain,
                    "event_file": event_path.relative_to(ROOT).as_posix(),
                    "event_name": build_site.require_str(event_path, event, "name", "event"),
                    "event_types": sorted(current_event_types),
                    "categories": list(metadata.categories),
                    "topics": list(metadata.topics),
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                    "status": build_site.optional_str(event, "status") or "confirmed",
                    "last_checked": last_checked.isoformat() if last_checked else None,
                    "reasons": reasons,
                    "urls_to_check": urls_to_check(metadata, event),
                }
            )

    return sorted(candidates, key=lambda item: (item["start"] or "9999-99-99", item["series"], item["event_name"]))


def update_reasons(
    event: dict,
    start: date | None,
    end: date | None,
    last_checked: date | None,
    reference_date: date,
    stale_before: date,
    past_cutoff: date,
) -> list[str]:
    reasons: list[str] = []
    effective_end = end or start
    is_future_or_recent = effective_end is None or effective_end >= past_cutoff
    if not is_future_or_recent:
        return reasons

    if start is None or end is None:
        reasons.append("date-tbd")
    if is_future(start, reference_date) and not build_site.optional_str(event, "venue"):
        reasons.append("missing-venue")
    if is_future(start, reference_date) and not any(build_site.optional_str(event, key) for key in ("city", "country")):
        reasons.append("missing-location")
    if is_future(start, reference_date) and has_location_hint(event) and not has_coordinates(event):
        reasons.append("missing-coordinates")
    if last_checked is None:
        reasons.append("never-checked")
    elif last_checked < stale_before and is_future_or_recent:
        reasons.append("stale-check")
    if is_future(start, reference_date) and not event.get("deadlines"):
        reasons.append("missing-deadlines")

    for deadline in event.get("deadlines") or []:
        deadline_date = build_site.optional_date(Path("<deadline>"), deadline, "date", "deadline")
        if reference_date <= deadline_date <= reference_date + timedelta(days=30):
            reasons.append("deadline-approaching")
        if reference_date - timedelta(days=14) <= deadline_date < reference_date:
            reasons.append("deadline-recently-passed")

    if end and reference_date - timedelta(days=31) <= end < reference_date:
        reasons.append("event-recently-completed")
    return sorted(set(reasons))


def latest_checked(metadata: build_site.SeriesMetadata, event_path: Path, event: dict) -> date | None:
    dates = [*metadata.checked_dates, *build_site.source_checked_dates(event_path, event, "event")]
    for deadline in event.get("deadlines") or []:
        dates.extend(build_site.source_checked_dates(event_path, deadline, "deadline"))
    return build_site.latest_date(dates)


def urls_to_check(metadata: build_site.SeriesMetadata, event: dict) -> list[str]:
    urls = [build_site.optional_str(event, "url"), metadata.website, *metadata.sources]
    urls.extend(source.get("url", "") for source in event.get("sources") or [] if isinstance(source, dict))
    for deadline in event.get("deadlines") or []:
        if not isinstance(deadline, dict):
            continue
        urls.append(build_site.optional_str(deadline, "url"))
        urls.extend(source.get("url", "") for source in deadline.get("sources") or [] if isinstance(source, dict))
        urls.extend(history.get("url", "") for history in deadline.get("history") or [] if isinstance(history, dict))
    return list(build_site.unique_values(url for url in urls if url))


def date_range_matches(
    start: date | None,
    end: date | None,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    effective_start = start or end
    effective_end = end or start
    if effective_end and date_from and effective_end < date_from:
        return False
    if effective_start and date_to and effective_start > date_to:
        return False
    return True


def is_future(start: date | None, reference_date: date) -> bool:
    return start is None or start >= reference_date


def has_location_hint(event: dict) -> bool:
    return any(build_site.optional_str(event, key) for key in ("venue", "address", "city"))


def has_coordinates(event: dict) -> bool:
    return "latitude" in event and "longitude" in event


def expand_filter(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        expanded.update(part.strip() for part in value.split(",") if part.strip())
    return expanded


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from exc


if __name__ == "__main__":
    raise SystemExit(main())
