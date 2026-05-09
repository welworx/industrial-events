from __future__ import annotations

import logging
import os
import subprocess
from datetime import date, datetime
from pathlib import Path

from industrial_events.config import BuildConfig, normalize_datetime, parse_iso_datetime
from industrial_events.dataset import (
    load_event_dataset,
)
from industrial_events.dataset import (
    write_event_dataset as write_event_dataset_artifact,
)
from industrial_events.event_pages import write_event_pages
from industrial_events.feeds import (
    build_feeds,
    clean_stale_feeds,
    render_calendar,
    write_index,
    write_rss_feed,
    write_site_index,
)
from industrial_events.models import EventDataset, Feed
from industrial_events.readme import write_readme_overview
from industrial_events.validation import CalendarBuildError

ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("build_site")


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(levelname)s %(message)s", force=True)


def build_site(
    config: BuildConfig,
    updated_at: datetime | None = None,
    reference_date: date | None = None,
    dataset: EventDataset | None = None,
    update_readme: bool = True,
) -> list[Feed]:
    LOGGER.info("Building event outputs")
    LOGGER.info("Source directory: %s", config.source_dir)
    LOGGER.info("Output directory: %s", config.output_dir)
    if dataset is None and not config.source_dir.exists():
        raise CalendarBuildError(f"source directory does not exist: {config.source_dir}")
    site_root = site_root_for_output_dir(config.output_dir)

    build_timestamp = feed_updated_at(config) if updated_at is None else normalize_datetime(updated_at)
    LOGGER.info("RSS build timestamp: %s", build_timestamp.isoformat())

    dataset = dataset or load_event_dataset(config)
    items = dataset.items
    event_count = sum(1 for item in items if item.kind == "event")
    deadline_count = len(items) - event_count
    LOGGER.info(
        "Loaded %d calendar item(s): %d event(s), %d deadline(s)",
        len(items),
        event_count,
        deadline_count,
    )

    feeds = build_feeds(items, config.output_dir)
    LOGGER.info("Built %d iCalendar feed definition(s)", len(feeds))

    undated_events = dataset.undated_events
    LOGGER.info("Loaded %d announced event(s) without calendar dates", len(undated_events))

    expected_feed_paths = {feed.path.resolve() for feed in feeds}
    stale_count = clean_stale_feeds(config.output_dir, expected_feed_paths, config, build_timestamp)
    LOGGER.info("Cleaned %d stale iCalendar feed(s)", stale_count)

    config.output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Writing %d iCalendar feed file(s)", len(feeds))
    for feed in feeds:
        feed.path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.debug("Writing iCalendar feed: %s (%d item(s))", feed.path, len(feed.items))
        feed.path.write_text(
            render_calendar(feed.name, feed.items, config, build_timestamp),
            encoding="utf-8",
            newline="\n",
        )

    LOGGER.info("Writing calendar feed index")
    write_index(config.output_dir, feeds)
    LOGGER.info("Writing event list pages")
    write_event_pages(site_root, items, undated_events, reference_date)
    if update_readme:
        LOGGER.info("Updating README overview sections")
        write_readme(config, dataset, reference_date)
    LOGGER.info("Writing RSS event stream")
    write_rss_feed(site_root, items, build_timestamp, config)
    LOGGER.info("Writing site index page")
    write_site_index(config.output_dir, feeds, config)
    return feeds


def site_root_for_output_dir(output_dir: Path) -> Path:
    if output_dir.name != "calendars":
        raise CalendarBuildError("output directory must be a generated 'calendars' directory")
    return output_dir.parent


def write_event_dataset(path: Path, dataset: EventDataset) -> None:
    write_event_dataset_artifact(path, dataset)
    LOGGER.info("Wrote event dataset artifact: %s", path)


def write_readme(
    config: BuildConfig,
    dataset: EventDataset,
    reference_date: date | None = None,
) -> None:
    write_readme_overview(
        config.readme_path,
        config.source_dir,
        config.sources_dir,
        dataset.items,
        dataset.undated_events,
        config,
        reference_date,
        series_metadata=dataset.series_metadata,
        source_pages=dataset.source_pages,
    )


def feed_updated_at(config: BuildConfig) -> datetime:
    env_value = os.environ.get(config.rss_updated_env, "").strip()
    if env_value:
        return parse_iso_datetime(env_value, config.rss_updated_env, CalendarBuildError)

    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%cI"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return config.default_feed_updated

    value = completed.stdout.strip()
    if not value:
        return config.default_feed_updated
    return parse_iso_datetime(value, "git commit date", CalendarBuildError)
