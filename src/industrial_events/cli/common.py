from __future__ import annotations

import argparse
from datetime import date

from industrial_events.validation import expand_slug_filter as expand_slug_filter


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from exc
