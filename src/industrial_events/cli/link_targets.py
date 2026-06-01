from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.link_checks import recent_event_link_targets, render_link_targets
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write recent event links for external link checking.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--years-back", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Include all historical event links.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        config = config_with_overrides(load_build_config(args.config), source_dir=args.source)
        targets = recent_event_link_targets(
            config.source_dir,
            reference_date=args.reference_date,
            years_back=None if args.all else args.years_back,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            render_link_targets(targets, title="Event Link Targets" if args.all else "Recent Event Link Targets"),
            encoding="utf-8",
            newline="\n",
        )
    except (CalendarBuildError, ConfigError, OSError) as exc:
        print(f"ERROR Link target generation failed: {exc}", file=sys.stderr)
        return 1
    return 0
