from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from industrial_events.url_utils import is_safe_external_url

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "site.yaml"
YAML_BOOL_TAG = "tag:yaml.org,2002:bool"


class EventYamlLoader(yaml.SafeLoader):
    pass


EventYamlLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in resolvers if tag != YAML_BOOL_TAG]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
EventYamlLoader.add_implicit_resolver(
    YAML_BOOL_TAG,
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


class ConfigError(Exception):
    """Raised when the site build configuration is invalid."""


@dataclass(frozen=True)
class BuildConfig:
    source_dir: Path
    output_dir: Path
    readme_path: Path
    sources_dir: Path
    site_title: str
    site_url: str
    repository_url: str
    product_id: str
    uid_domain: str
    rss_updated_env: str
    default_feed_updated: datetime
    disclaimer: str


def load_build_config(path: Path = DEFAULT_CONFIG_PATH) -> BuildConfig:
    data = load_config_yaml(path)
    site = required_mapping(data, "site", path)
    paths = required_mapping(data, "paths", path)

    configured_site_url = env_url_override(site, "url", "INDUSTRIAL_EVENTS_SITE_URL", path)

    return BuildConfig(
        source_dir=config_path(path, paths, "source_dir"),
        output_dir=config_path(path, paths, "output_dir"),
        readme_path=config_path(path, paths, "readme"),
        sources_dir=config_path(path, paths, "sources_dir"),
        site_title=required_str(site, "title", path),
        site_url=site_url(configured_site_url),
        repository_url=required_url(site, "repository_url", path),
        product_id=required_str(site, "product_id", path),
        uid_domain=required_str(site, "uid_domain", path),
        rss_updated_env=required_str(site, "rss_updated_env", path),
        default_feed_updated=parse_config_datetime(required_str(site, "default_feed_updated", path), path),
        disclaimer=required_str(site, "disclaimer", path),
    )


def config_with_overrides(
    config: BuildConfig,
    *,
    source_dir: Path | None = None,
    output_dir: Path | None = None,
    readme_path: Path | None = None,
    sources_dir: Path | None = None,
) -> BuildConfig:
    return replace(
        config,
        source_dir=source_dir or config.source_dir,
        output_dir=output_dir or config.output_dir,
        readme_path=readme_path or config.readme_path,
        sources_dir=sources_dir or config.sources_dir,
    )


def load_config_yaml(path: Path) -> dict[str, Any]:
    return load_yaml_mapping(path, error_type=ConfigError, read_label="config file")


def load_yaml_mapping(path: Path, *, error_type: type[Exception], read_label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = yaml.load(file, Loader=EventYamlLoader)
    except OSError as exc:
        raise error_type(f"{path}: cannot read {read_label}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise error_type(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise error_type(f"{path}: expected a YAML object")
    return data


def repo_relative_path(path: Path, root: Path = ROOT) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def required_mapping(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: config field {key!r} must be a YAML object")
    return value


def required_str(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: config field {key!r} is required")
    return value.strip()


def required_url(data: dict[str, Any], key: str, path: Path) -> str:
    value = required_str(data, key, path)
    if not is_safe_external_url(value):
        raise ConfigError(f"{path}: config field {key!r} must be an http(s) URL")
    return value


def env_url_override(data: dict[str, Any], key: str, env_name: str, path: Path) -> str:
    value = os.environ.get(env_name) or required_url(data, key, path)
    if not is_safe_external_url(value):
        raise ConfigError(f"{env_name} must be an http(s) URL")
    return value


def config_path(config_file: Path, data: dict[str, Any], key: str) -> Path:
    raw_path = required_str(data, key, config_file)
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return config_file.parent / path


def site_url(value: str) -> str:
    if value.endswith("/"):
        return value
    return value + "/"


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_iso_datetime(value: str, label: str, error_type: type[Exception]) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise error_type(f"{label} must be an ISO 8601 date-time") from exc
    return normalize_datetime(parsed)


def parse_config_datetime(value: str, path: Path) -> datetime:
    return parse_iso_datetime(value, f"{path}: default_feed_updated", ConfigError)
