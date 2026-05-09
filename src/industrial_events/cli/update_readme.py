from __future__ import annotations

import argparse
import sys
from pathlib import Path

from industrial_events.builder import configure_logging, write_readme
from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.dataset import read_event_dataset
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update README generated sections from a dataset artifact.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    try:
        config = config_with_overrides(load_build_config(args.config), readme_path=args.readme)
        write_readme(config, read_event_dataset(args.dataset))
    except (CalendarBuildError, ConfigError) as exc:
        print(f"ERROR Build failed: {exc}", file=sys.stderr)
        return 1
    return 0
