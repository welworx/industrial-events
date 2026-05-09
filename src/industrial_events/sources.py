from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from industrial_events import data
from industrial_events.models import SourcePage
from industrial_events.url_utils import is_safe_external_url
from industrial_events.validation import (
    SLUG_RE,
    SOURCE_PAGE_KEYS,
    optional_date,
    optional_slug_list,
    optional_str,
    require_slug,
    require_str,
    require_url,
    validate_unknown_keys,
)

load_yaml = data.load_yaml


def load_source_pages(source_dir: Path) -> list[SourcePage]:
    if not source_dir.exists():
        return []

    pages: list[SourcePage] = []
    for path in sorted(source_dir.glob("*/*.yaml")):
        source = load_yaml(path)
        validate_unknown_keys(path, source, SOURCE_PAGE_KEYS)
        pages.append(
            SourcePage(
                name=require_str(path, source, "name"),
                slug=require_slug(path, source, "slug"),
                url=require_url(path, source, "url"),
                type=require_slug(path, source, "type"),
                categories=optional_slug_list(path, source, "categories"),
                topics=optional_slug_list(path, source, "topics"),
                last_checked=optional_date(path, source, "last_checked", "top level"),
                last_updated=optional_date(path, source, "last_updated", "top level"),
                note=optional_str(source, "note"),
            )
        )
    return pages


def source_page_to_json(page: SourcePage) -> dict[str, object]:
    return {
        "name": page.name,
        "slug": page.slug,
        "url": page.url,
        "type": page.type,
        "categories": list(page.categories),
        "topics": list(page.topics),
        "last_checked": page.last_checked.isoformat() if page.last_checked else None,
        "last_updated": page.last_updated.isoformat() if page.last_updated else None,
        "note": page.note,
    }


def source_page_from_json(value: dict) -> SourcePage:
    if not isinstance(value, dict):
        raise TypeError("SourcePage must be a JSON object")
    url = _require_string(value, "url")
    if not is_safe_external_url(url):
        raise ValueError("SourcePage.url must be an http(s) URL")
    slug = _require_string(value, "slug")
    source_type = _require_string(value, "type")
    categories = _require_string_tuple(value, "categories")
    topics = _require_string_tuple(value, "topics")
    _validate_slug(slug, "SourcePage.slug")
    _validate_slug(source_type, "SourcePage.type")
    _validate_slug_tuple(categories, "SourcePage.categories")
    _validate_slug_tuple(topics, "SourcePage.topics")
    return SourcePage(
        name=_require_string(value, "name"),
        slug=slug,
        url=url,
        type=source_type,
        categories=categories,
        topics=topics,
        last_checked=_optional_date(value, "last_checked"),
        last_updated=_optional_date(value, "last_updated"),
        note=_require_string(value, "note"),
    )


def _require_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping[key]
    if not isinstance(value, str):
        raise TypeError(f"SourcePage.{key} must be a string")
    return value


def _require_string_tuple(mapping: dict[str, Any], key: str) -> tuple[str, ...]:
    value = mapping[key]
    if not isinstance(value, list):
        raise TypeError(f"SourcePage.{key} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"SourcePage.{key} must contain only strings")
    return tuple(value)


def _optional_date(mapping: dict[str, Any], key: str) -> date | None:
    value = mapping[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"SourcePage.{key} must be a date string or null")
    return date.fromisoformat(value)


def _validate_slug(value: str, label: str) -> None:
    if not SLUG_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase slug")


def _validate_slug_tuple(values: tuple[str, ...], label: str) -> None:
    if not all(SLUG_RE.fullmatch(value) for value in values):
        raise ValueError(f"{label} must contain only lowercase slugs")
