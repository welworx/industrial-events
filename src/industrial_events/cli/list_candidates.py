from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from industrial_events.cli.common import expand_slug_filter, parse_date_arg
from industrial_events.config import DEFAULT_CONFIG_PATH, ConfigError, config_with_overrides, load_build_config
from industrial_events.update_candidates import list_candidates
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List event YAML files that are likely to need verification.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--source", type=Path)
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

    try:
        series = expand_slug_filter(args.series)
        categories = expand_slug_filter(args.category)
        event_types = expand_slug_filter(args.event_type)
        config = config_with_overrides(load_build_config(args.config), source_dir=args.source)
        candidates = list_candidates(
            config.source_dir,
            series=series,
            categories=categories,
            event_types=event_types,
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
                "series": sorted(series),
                "categories": sorted(categories),
                "event_types": sorted(event_types),
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
    except (CalendarBuildError, ConfigError, OSError) as exc:
        print(f"ERROR Candidate listing failed: {exc}", file=sys.stderr)
        return 1
