from __future__ import annotations

import argparse
from pathlib import Path

from industrial_events.builder import (
    LOGGER,
    build_site,
    configure_logging,
    load_event_dataset,
    write_event_dataset,
    write_readme,
)
from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.dataset import read_event_dataset
from industrial_events.models import Feed
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build static site outputs from event YAML files.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--dataset", type=Path, help="Read normalized event data from a JSON dataset artifact.")
    parser.add_argument("--write-dataset", type=Path, help="Write normalized event data to a JSON dataset artifact.")
    parser.add_argument("--skip-readme", action="store_true", help="Build site outputs without updating README.md.")
    parser.add_argument("--readme-only", action="store_true", help="Update README.md without writing site outputs.")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = config_with_overrides(
            load_build_config(args.config),
            source_dir=args.source,
            output_dir=args.output,
            readme_path=args.readme,
            sources_dir=args.sources,
        )
        dataset = read_event_dataset(args.dataset) if args.dataset else load_event_dataset(config)
        if args.write_dataset:
            write_event_dataset(args.write_dataset, dataset)
        if args.readme_only:
            write_readme(config, dataset)
            feeds: list[Feed] = []
        else:
            feeds = build_site(config, dataset=dataset, update_readme=not args.skip_readme)
    except (CalendarBuildError, ConfigError) as exc:
        LOGGER.error("Build failed: %s", exc)
        return 1

    LOGGER.info("Generated %d calendar feed(s) in %s", len(feeds), config.output_dir)
    return 0
