from __future__ import annotations

import json
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any

from industrial_events import data
from industrial_events.config import BuildConfig
from industrial_events.models import CalendarItem, EventDataset, SeriesMetadata, UndatedEvent
from industrial_events.sources import load_source_pages, source_page_from_json, source_page_to_json
from industrial_events.url_utils import is_safe_external_url
from industrial_events.validation import SLUG_RE, CalendarBuildError

load_items = data.load_items
load_series_metadata = data.load_series_metadata
load_undated_events = data.load_undated_events


def load_event_dataset(config: BuildConfig) -> EventDataset:
    return EventDataset(
        items=tuple(load_items(config.source_dir, config)),
        undated_events=tuple(load_undated_events(config.source_dir)),
        series_metadata=tuple(load_series_metadata(config.source_dir)),
        source_pages=tuple(load_source_pages(config.sources_dir)),
    )


def write_event_dataset(path: Path, dataset: EventDataset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(event_dataset_to_json(dataset), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_event_dataset(path: Path) -> EventDataset:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return event_dataset_from_json(payload)
    except OSError as exc:
        raise CalendarBuildError(f"{path}: cannot read event dataset artifact: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CalendarBuildError(f"{path}: invalid event dataset artifact: invalid JSON: {exc.msg}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise CalendarBuildError(f"{path}: invalid event dataset artifact: {exc}") from exc


def event_dataset_to_json(dataset: EventDataset) -> dict[str, object]:
    return {
        "items": [calendar_item_to_json(item) for item in dataset.items],
        "undated_events": [undated_event_to_json(item) for item in dataset.undated_events],
        "series_metadata": [series_metadata_to_json(item) for item in dataset.series_metadata],
        "source_pages": [source_page_to_json(page) for page in dataset.source_pages],
    }


def event_dataset_from_json(value: dict) -> EventDataset:
    mapping = _require_mapping(value, "dataset")
    return EventDataset(
        items=tuple(calendar_item_from_json(item) for item in _require_list(mapping, "items", "dataset")),
        undated_events=tuple(
            undated_event_from_json(item) for item in _require_list(mapping, "undated_events", "dataset")
        ),
        series_metadata=tuple(
            series_metadata_from_json(item) for item in _require_list(mapping, "series_metadata", "dataset")
        ),
        source_pages=tuple(source_page_from_json(page) for page in _require_list(mapping, "source_pages", "dataset")),
    )


def calendar_item_to_json(item: CalendarItem) -> dict[str, object]:
    return _dataclass_to_json(
        item,
        date_fields={"start", "end_exclusive", "last_checked"},
        tuple_fields={"categories", "topics", "event_types", "co_location_series"},
    )


def calendar_item_from_json(value: dict) -> CalendarItem:
    item = _dataclass_from_json(
        CalendarItem,
        value,
        date_fields={"start", "end_exclusive", "last_checked"},
        tuple_fields={"categories", "topics", "event_types", "co_location_series"},
        string_fields={
            "uid",
            "summary",
            "series",
            "series_slug",
            "domain",
            "country",
            "kind",
            "status",
            "location",
            "city",
            "venue",
            "address",
            "url",
            "description",
            "co_location_group",
            "co_location_name",
            "co_location_url",
        },
        number_fields={"latitude", "longitude"},
    )
    _validate_optional_url(item.url, "CalendarItem.url")
    _validate_optional_url(item.co_location_url, "CalendarItem.co_location_url")
    _validate_slug(item.series_slug, "CalendarItem.series_slug")
    _validate_slug(item.domain, "CalendarItem.domain")
    _validate_optional_slug(item.country, "CalendarItem.country")
    _validate_slug(item.kind, "CalendarItem.kind")
    _validate_optional_slug(item.co_location_group, "CalendarItem.co_location_group")
    _validate_slug_tuple(item.categories, "CalendarItem.categories")
    _validate_slug_tuple(item.topics, "CalendarItem.topics")
    _validate_slug_tuple(item.event_types, "CalendarItem.event_types")
    _validate_slug_tuple(item.co_location_series, "CalendarItem.co_location_series")
    return item


def undated_event_to_json(item: UndatedEvent) -> dict[str, object]:
    return _dataclass_to_json(
        item,
        date_fields={"last_checked"},
        tuple_fields={"categories", "event_types"},
    )


def undated_event_from_json(value: dict) -> UndatedEvent:
    item = _dataclass_from_json(
        UndatedEvent,
        value,
        date_fields={"last_checked"},
        tuple_fields={"categories", "event_types"},
        string_fields={
            "series_slug",
            "domain",
            "title",
            "url",
            "scope",
            "location",
            "source_url",
            "co_location_group",
        },
    )
    _validate_optional_url(item.url, "UndatedEvent.url")
    _validate_optional_url(item.source_url, "UndatedEvent.source_url")
    _validate_slug(item.series_slug, "UndatedEvent.series_slug")
    _validate_slug(item.domain, "UndatedEvent.domain")
    _validate_optional_slug(item.co_location_group, "UndatedEvent.co_location_group")
    _validate_slug_tuple(item.categories, "UndatedEvent.categories")
    _validate_slug_tuple(item.event_types, "UndatedEvent.event_types")
    return item


def series_metadata_to_json(item: SeriesMetadata) -> dict[str, object]:
    return _dataclass_to_json(
        item,
        date_fields={"checked_dates"},
        tuple_fields={"categories", "topics", "sources", "checked_dates"},
        path_fields={"path"},
    )


def series_metadata_from_json(value: dict) -> SeriesMetadata:
    item = _dataclass_from_json(
        SeriesMetadata,
        value,
        date_fields={"checked_dates"},
        tuple_fields={"categories", "topics", "sources", "checked_dates"},
        path_fields={"path"},
        string_fields={"domain", "series", "slug", "description", "recurrence", "website"},
    )
    _validate_optional_url(item.website, "SeriesMetadata.website")
    _validate_slug(item.domain, "SeriesMetadata.domain")
    _validate_slug(item.slug, "SeriesMetadata.slug")
    _validate_slug_tuple(item.categories, "SeriesMetadata.categories")
    _validate_slug_tuple(item.topics, "SeriesMetadata.topics")
    for source in item.sources:
        _validate_required_url(source, "SeriesMetadata.sources")
    return item


def _dataclass_to_json(
    instance: Any,
    *,
    date_fields: set[str],
    tuple_fields: set[str],
    path_fields: set[str] = frozenset(),
) -> dict[str, object]:
    result: dict[str, object] = {}
    for field in fields(instance):
        value = getattr(instance, field.name)
        if field.name in path_fields:
            result[field.name] = str(value)
        elif field.name in date_fields and isinstance(value, date):
            result[field.name] = value.isoformat()
        elif field.name in tuple_fields:
            result[field.name] = [
                item.isoformat() if field.name in date_fields and isinstance(item, date) else item for item in value
            ]
        elif field.name in date_fields and value is None:
            result[field.name] = None
        else:
            result[field.name] = value
    return result


def _dataclass_from_json(
    cls: type[Any],
    value: dict,
    *,
    date_fields: set[str],
    tuple_fields: set[str],
    path_fields: set[str] = frozenset(),
    string_fields: set[str] = frozenset(),
    number_fields: set[str] = frozenset(),
) -> Any:
    mapping = _require_mapping(value, cls.__name__)
    parsed: dict[str, Any] = {}
    for field in fields(cls):
        if field.name not in mapping:
            raise ValueError(f"{cls.__name__}.{field.name} is required")
        item = mapping[field.name]
        if field.name in path_fields:
            if not isinstance(item, str):
                raise TypeError(f"{cls.__name__}.{field.name} must be a string path")
            parsed[field.name] = Path(item)
        elif field.name in tuple_fields:
            if not isinstance(item, list):
                raise TypeError(f"{cls.__name__}.{field.name} must be a list")
            parsed[field.name] = tuple(
                _parse_date(entry, f"{cls.__name__}.{field.name}")
                if field.name in date_fields
                else _require_list_string(
                    entry,
                    f"{cls.__name__}.{field.name}",
                )
                for entry in item
            )
        elif field.name in date_fields:
            parsed[field.name] = None if item is None else _parse_date(item, f"{cls.__name__}.{field.name}")
        elif field.name in string_fields:
            parsed[field.name] = _require_string(item, f"{cls.__name__}.{field.name}")
        elif field.name in number_fields:
            parsed[field.name] = _optional_float(item, f"{cls.__name__}.{field.name}")
        else:
            parsed[field.name] = item
    return cls(**parsed)


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _require_list(mapping: dict[str, Any], key: str, label: str) -> list[Any]:
    value = mapping.get(key)
    if not isinstance(value, list):
        raise TypeError(f"{label}.{key} must be a list")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _require_list_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must contain only strings")
    return value


def _parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a date string")
    return date.fromisoformat(value)


def _optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{label} must be a number or null")
    return float(value)


def _validate_optional_url(value: Any, label: str) -> None:
    if value in ("", None):
        return
    _validate_required_url(value, label)


def _validate_required_url(value: Any, label: str) -> None:
    if not isinstance(value, str) or not is_safe_external_url(value):
        raise ValueError(f"{label} must be an http(s) URL")


def _validate_optional_slug(value: str, label: str) -> None:
    if value:
        _validate_slug(value, label)


def _validate_slug(value: str, label: str) -> None:
    if not SLUG_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase slug")


def _validate_slug_tuple(values: tuple[str, ...], label: str) -> None:
    if not all(SLUG_RE.fullmatch(value) for value in values):
        raise ValueError(f"{label} must contain only lowercase slugs")
