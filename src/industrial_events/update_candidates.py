from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from industrial_events.config import repo_relative_path
from industrial_events.data import (
    event_files,
    load_event_file,
    load_series_metadata_file,
    series_metadata_files,
    unique_values,
)
from industrial_events.models import SeriesMetadata
from industrial_events.validation import (
    CalendarBuildError,
    optional_date,
    optional_str,
    require_slug_list,
    require_str,
    source_checked_dates,
    validate_event_record,
)
from industrial_events.validation import (
    latest_date as latest_known_date,
)


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
    if not source_dir.exists():
        raise CalendarBuildError(f"source directory does not exist: {source_dir}")

    candidates: list[dict[str, Any]] = []
    stale_before = reference_date - timedelta(days=stale_after_days)
    past_cutoff = reference_date - timedelta(days=past_grace_days)

    for metadata_path in series_metadata_files(source_dir):
        metadata = load_series_metadata_file(source_dir, metadata_path)
        if series and metadata.slug not in series:
            continue
        if categories and not categories.intersection(metadata.categories):
            continue

        for event_path in event_files(metadata_path):
            event = load_event_file(event_path)
            validate_event_record(event_path, event)
            current_event_types = set(require_slug_list(event_path, event, "event_types", "event"))
            if event_types and not event_types.intersection(current_event_types):
                continue

            start = optional_date(event_path, event, "start", "event")
            end = optional_date(event_path, event, "end", "event")
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
                    "event_file": repo_relative_path(event_path),
                    "event_name": require_str(event_path, event, "name", "event"),
                    "event_types": sorted(current_event_types),
                    "categories": list(metadata.categories),
                    "topics": list(metadata.topics),
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if end else None,
                    "status": optional_str(event, "status") or "confirmed",
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
    if is_future(start, reference_date) and not optional_str(event, "venue"):
        reasons.append("missing-venue")
    if is_future(start, reference_date) and not any(optional_str(event, key) for key in ("city", "country")):
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
        deadline_date = optional_date(Path("<deadline>"), deadline, "date", "deadline")
        if reference_date <= deadline_date <= reference_date + timedelta(days=30):
            reasons.append("deadline-approaching")
        if reference_date - timedelta(days=14) <= deadline_date < reference_date:
            reasons.append("deadline-recently-passed")

    if end and reference_date - timedelta(days=31) <= end < reference_date:
        reasons.append("event-recently-completed")
    return sorted(set(reasons))


def latest_checked(metadata: SeriesMetadata, event_path: Path, event: dict) -> date | None:
    dates = [*metadata.checked_dates, *source_checked_dates(event_path, event, "event")]
    for deadline in event.get("deadlines") or []:
        dates.extend(source_checked_dates(event_path, deadline, "deadline"))
    return latest_known_date(dates)


def urls_to_check(metadata: SeriesMetadata, event: dict) -> list[str]:
    urls = [optional_str(event, "url"), metadata.website, *metadata.sources]
    urls.extend(source.get("url", "") for source in event.get("sources") or [] if isinstance(source, dict))
    for deadline in event.get("deadlines") or []:
        if not isinstance(deadline, dict):
            continue
        urls.append(optional_str(deadline, "url"))
        urls.extend(source.get("url", "") for source in deadline.get("sources") or [] if isinstance(source, dict))
        urls.extend(history.get("url", "") for history in deadline.get("history") or [] if isinstance(history, dict))
    return list(unique_values(url for url in urls if url))


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
    return any(optional_str(event, key) for key in ("venue", "address", "city"))


def has_coordinates(event: dict) -> bool:
    return "latitude" in event and "longitude" in event
