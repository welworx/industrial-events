from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from industrial_events.config import load_build_config
from industrial_events.data import (
    event_files,
    load_event_file,
    load_series_metadata_file,
    load_yaml,
    series_metadata_files,
)
from industrial_events.url_utils import safe_external_url
from industrial_events.validation import optional_date

EVENT_YEAR_RE = re.compile(r"(?:^|-)(\d{4})(?:-|$)")
LINK_CHECK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class LinkTarget:
    path: Path
    label: str
    url: str


@dataclass(frozen=True)
class LinkCheckFailure:
    target: LinkTarget
    error: str


def recent_event_link_targets(
    source_dir: Path,
    *,
    reference_date: date,
    years_back: int | None = 1,
) -> tuple[LinkTarget, ...]:
    cutoff = None if years_back is None else date(reference_date.year - years_back, 1, 1)
    targets: list[LinkTarget] = []
    for metadata_path in series_metadata_files(source_dir):
        metadata = load_series_metadata_file(source_dir, metadata_path)
        metadata_targets = tuple(yaml_link_targets(metadata_path))
        for event_path in event_files(metadata_path):
            event = load_event_file(event_path)
            start = optional_date(event_path, event, "start", "event")
            end = optional_date(event_path, event, "end", "event")
            if cutoff is not None and not event_is_recent(event_path, start, end, cutoff):
                continue
            targets.extend(metadata_targets)
            targets.extend(yaml_link_targets(event_path))
            if metadata.website:
                targets.append(LinkTarget(metadata_path, "website", metadata.website))
            targets.extend(LinkTarget(metadata_path, "source", url) for url in metadata.sources)
    return unique_link_targets(targets)


def event_is_recent(event_path: Path, start: date | None, end: date | None, cutoff: date) -> bool:
    effective_end = end or start
    if effective_end is not None:
        return effective_end >= cutoff

    event_year = event_year_from_path(event_path)
    return event_year is None or event_year >= cutoff.year


def event_year_from_path(path: Path) -> int | None:
    match = EVENT_YEAR_RE.search(path.stem)
    return int(match.group(1)) if match else None


def yaml_link_targets(path: Path) -> tuple[LinkTarget, ...]:
    return tuple(_walk_link_targets(path, load_yaml(path)))


def _walk_link_targets(path: Path, value: Any, label: str = "top level") -> Iterator[LinkTarget]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"url", "website"} and is_inactive_link(value, key):
                continue
            child_label = f"{label}.{key}" if label else str(key)
            yield from _walk_link_targets(path, item, child_label)
    elif isinstance(value, list):
        for index, item in enumerate(value, start=1):
            yield from _walk_link_targets(path, item, f"{label}[{index}]")
    elif isinstance(value, str):
        url = safe_external_url(value)
        if url:
            yield LinkTarget(path, label, url)


def is_inactive_link(data: dict, key: str) -> bool:
    status_key = "url_status" if key == "url" else "website_status"
    status = data.get(status_key)
    return isinstance(status, str) and status.lower() in {"inactive", "restricted"}


def unique_link_targets(targets: Iterable[LinkTarget]) -> tuple[LinkTarget, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[LinkTarget] = []
    for target in targets:
        key = (target.path.as_posix(), target.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return tuple(unique)


def render_link_targets(targets: Iterable[LinkTarget], *, title: str) -> str:
    lines = [f"# {title}", ""]
    for target in targets:
        lines.append(f"- `{target.path.as_posix()}` `{target.label}`: <{target.url}>")
    return "\n".join(lines) + "\n"


def staged_added_yaml_files(root: Path | None = None) -> tuple[Path, ...]:
    root = root or load_build_config().source_dir.parent
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(
        root / path for path in completed.stdout.splitlines() if Path(path).suffix.lower() in {".yaml", ".yml"}
    )


def check_link_targets(
    targets: Iterable[LinkTarget],
    *,
    timeout: float = 10.0,
    max_workers: int = 4,
) -> tuple[LinkCheckFailure, ...]:
    targets_tuple = tuple(targets)
    urls = tuple(dict.fromkeys(target.url for target in targets_tuple))
    if not urls:
        return ()

    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {executor.submit(check_url, url, timeout=timeout): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            error = future.result()
            if error:
                errors[url] = error

    return tuple(LinkCheckFailure(target, errors[target.url]) for target in targets_tuple if target.url in errors)


def check_url(url: str, *, timeout: float) -> str:
    try:
        return _check_url(url, "HEAD", timeout)
    except HTTPError as exc:
        if exc.code not in {403, 405}:
            return f"HTTP {exc.code}"
    except URLError as exc:
        return str(exc.reason)
    except (OSError, ValueError) as exc:
        return str(exc)

    try:
        return _check_url(url, "GET", timeout)
    except HTTPError as exc:
        return f"HTTP {exc.code}"
    except URLError as exc:
        return str(exc.reason)
    except (OSError, ValueError) as exc:
        return str(exc)


def _check_url(url: str, method: str, timeout: float) -> str:
    headers = {"User-Agent": LINK_CHECK_USER_AGENT}
    if method == "GET":
        headers["Range"] = "bytes=0-0"
    request = Request(url, method=method, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        status = response.status
    if status >= 400:
        return f"HTTP {status}"
    return ""
