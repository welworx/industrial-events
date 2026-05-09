from __future__ import annotations

import re
from datetime import date, timedelta

from industrial_events.url_utils import safe_external_url


def format_date_range(start: date, end_exclusive: date) -> str:
    end_inclusive = end_exclusive - timedelta(days=1)
    if start == end_inclusive:
        return start.isoformat()
    return f"{start.isoformat()} to {end_inclusive.isoformat()}"


def format_last_checked(value: date | None) -> str:
    if value is None:
        return "TBD"
    return value.isoformat()


def markdown_link(label: str, url: str) -> str:
    escaped_label = escape_markdown_table(label).replace("[", "\\[").replace("]", "\\]")
    safe_url = safe_external_url(url)
    if not safe_url:
        return escaped_label
    escaped_url = safe_url.replace("<", "%3C").replace(">", "%3E")
    return f"[{escaped_label}](<{escaped_url}>)"


def html_link(label: str, url: str) -> str:
    escaped_label = escape_html(label)
    safe_url = safe_external_url(url)
    if not safe_url:
        return escaped_label
    return f'<a href="{escape_html(safe_url)}">{escaped_label}</a>'


def html_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def escape_markdown_table(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r\n", " ").replace("\n", " ").replace("|", "\\|")


def escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
