from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from demolytics.domain.stats import SUPPORTED_STAT_KEYS

APP_DIR_NAME = "Demolytics"
SETTINGS_FILE_NAME = "settings.json"
DEFAULT_PORT = 49123

DEFAULT_GLANCE_STATS: tuple[str, ...] = (
    "shooting_percentage",
    "demos_inflicted",
    "demos_taken",
    "avg_boost",
    "avg_speed",
    "airborne_percentage",
)


@dataclass
class AppSettings:
    websocket_port: int = DEFAULT_PORT
    visible_stats: list[str] = field(default_factory=lambda: list(SUPPORTED_STAT_KEYS))
    glance_stats: list[str] = field(default_factory=lambda: list(DEFAULT_GLANCE_STATS))
    database_path: str | None = None
    install_dir: str | None = None


def get_app_data_dir() -> Path:
    app_data = os.getenv("APPDATA")
    if app_data:
        return Path(app_data) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def get_default_database_path() -> Path:
    return get_app_data_dir() / "demolytics.db"


def get_settings_path() -> Path:
    return get_app_data_dir() / SETTINGS_FILE_NAME


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or get_settings_path()
    if not settings_path.exists():
        settings = AppSettings(database_path=str(get_default_database_path()))
        return settings

    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    settings = AppSettings(**_coerce_known_settings(raw))
    if settings.database_path is None:
        settings.database_path = str(get_default_database_path())
    return settings


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings_path = path or get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(asdict(settings), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _coerce_known_settings(raw: dict[str, Any]) -> dict[str, Any]:
    visible_stats = raw.get("visible_stats")
    if not isinstance(visible_stats, list):
        visible_stats = list(SUPPORTED_STAT_KEYS)

    glance_stats = raw.get("glance_stats")
    if not isinstance(glance_stats, list):
        glance_stats = list(DEFAULT_GLANCE_STATS)
    glance_stats = [str(key) for key in glance_stats if str(key) in SUPPORTED_STAT_KEYS]
    if not glance_stats:
        glance_stats = list(DEFAULT_GLANCE_STATS)

    visible_stats = [str(key) for key in visible_stats if str(key) in SUPPORTED_STAT_KEYS]
    if not visible_stats:
        visible_stats = list(SUPPORTED_STAT_KEYS)

    return {
        "websocket_port": int(raw.get("websocket_port", DEFAULT_PORT)),
        "visible_stats": visible_stats,
        "glance_stats": glance_stats,
        "database_path": raw.get("database_path"),
        "install_dir": raw.get("install_dir"),
    }
