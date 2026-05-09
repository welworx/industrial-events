from __future__ import annotations

import argparse
import sys
from pathlib import Path

from industrial_events.builder import configure_logging, write_event_dataset
from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.dataset import load_event_dataset
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a normalized event dataset artifact from YAML files.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = config_with_overrides(
            load_build_config(args.config),
            source_dir=args.source,
            sources_dir=args.sources,
        )
        write_event_dataset(args.output, load_event_dataset(config))
    except (CalendarBuildError, ConfigError) as exc:
        print(f"ERROR Build failed: {exc}", file=sys.stderr)
        return 1
    return 0
