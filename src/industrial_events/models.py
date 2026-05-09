from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class CalendarItem:
    uid: str
    summary: str
    start: date
    end_exclusive: date
    series: str
    series_slug: str
    domain: str
    categories: tuple[str, ...]
    topics: tuple[str, ...]
    country: str
    kind: str
    status: str
    event_types: tuple[str, ...] = ("conference",)
    location: str = ""
    city: str = ""
    venue: str = ""
    address: str = ""
    url: str = ""
    description: str = ""
    latitude: float | None = None
    longitude: float | None = None
    last_checked: date | None = None
    co_location_group: str = ""
    co_location_name: str = ""
    co_location_url: str = ""
    co_location_series: tuple[str, ...] = ()


@dataclass(frozen=True)
class UndatedEvent:
    series_slug: str
    domain: str
    categories: tuple[str, ...]
    title: str
    url: str
    scope: str
    location: str
    source_url: str
    last_checked: date | None = None
    co_location_group: str = ""
    event_types: tuple[str, ...] = ("conference",)


@dataclass(frozen=True)
class SeriesMetadata:
    path: Path
    domain: str
    series: str
    slug: str
    description: str
    recurrence: str
    categories: tuple[str, ...]
    topics: tuple[str, ...]
    website: str = ""
    sources: tuple[str, ...] = ()
    checked_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class CoLocation:
    group: str = ""
    name: str = ""
    url: str = ""
    series: tuple[str, ...] = ()

    @property
    def description(self) -> str:
        if not self.group:
            return ""
        if self.name:
            return f"{self.name} ({self.group})"
        return self.group


@dataclass(frozen=True)
class Feed:
    path: Path
    name: str
    items: tuple[CalendarItem, ...]


@dataclass(frozen=True)
class SourcePage:
    name: str
    slug: str
    url: str
    type: str
    categories: tuple[str, ...]
    topics: tuple[str, ...]
    last_checked: date | None = None
    last_updated: date | None = None
    note: str = ""


@dataclass(frozen=True)
class EventDataset:
    items: tuple[CalendarItem, ...]
    undated_events: tuple[UndatedEvent, ...]
    series_metadata: tuple[SeriesMetadata, ...]
    source_pages: tuple[SourcePage, ...]


@dataclass(frozen=True)
class MarkdownPage:
    path: Path
    title: str
    items: tuple[CalendarItem, ...]
    undated_events: tuple[UndatedEvent, ...] = ()


@dataclass(frozen=True)
class EventRow:
    start: date
    end_exclusive: date
    title: str
    url: str
    location: str
    last_checked: date | None
    conferences: tuple[CalendarItem, ...]
    deadlines: tuple[CalendarItem, ...]


@dataclass(frozen=True)
class SubmissionOpportunity:
    deadline: CalendarItem
    row: EventRow
