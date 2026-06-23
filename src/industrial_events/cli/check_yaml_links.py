from __future__ import annotations

import argparse
import sys
from pathlib import Path

from industrial_events.config import ConfigError
from industrial_events.link_checks import check_link_targets, staged_added_yaml_files, yaml_link_targets
from industrial_events.validation import CalendarBuildError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check external links in newly added YAML files.")
    parser.add_argument("paths", nargs="*", type=Path, help="YAML files to check. Defaults to staged added YAML files.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    paths = tuple(path for path in args.paths if path.suffix.lower() in {".yaml", ".yml"})
    if not args.paths:
        paths = staged_added_yaml_files()
    if not paths:
        return 0

    try:
        targets = tuple(target for path in paths for target in yaml_link_targets(path))
        failures = check_link_targets(targets, timeout=args.timeout, max_workers=args.workers)
    except (CalendarBuildError, ConfigError, OSError) as exc:
        print(f"ERROR YAML link check failed: {exc}", file=sys.stderr)
        return 1

    for failure in failures:
        target = failure.target
        print(f"{target.path.as_posix()} {target.label}: {target.url} ({failure.error})", file=sys.stderr)
    return 1 if failures else 0
