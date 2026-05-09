from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_EXTERNAL_URL_SCHEMES = {"http", "https"}


def safe_external_url(value: str) -> str:
    stripped = value.strip()
    if not stripped or any(char.isspace() for char in stripped):
        return ""
    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in ALLOWED_EXTERNAL_URL_SCHEMES or not parsed.netloc:
        return ""
    return stripped


def is_safe_external_url(value: str) -> bool:
    return bool(safe_external_url(value))
