from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import keyring
from keyring import errors as keyring_errors

from demolytics.domain.stats import (
    DEFAULT_STATS_TAB_VISIBLE,
    GLANCE_STAT_KEYS,
    STATS_TAB_COLUMN_KEYS,
    TEAM_STAT_KEYS,
)

APP_DIR_NAME = "Demolytics"
SETTINGS_FILE_NAME = "settings.json"
DEFAULT_PORT = 49123

DEFAULT_GLANCE_STATS: tuple[str, ...] = (
    "possession_percentage",
    "team_demos_inflicted",
    "team_demos_taken",
    "avg_boost",
    "avg_speed",
    "airborne_percentage",
)

# If settings still contain exactly this list (previous default), migrate to ``DEFAULT_GLANCE_STATS``.
_PREVIOUS_DEFAULT_GLANCE_STATS: tuple[str, ...] = (
    "shooting_percentage",
    "team_demos_inflicted",
    "team_demos_taken",
    "avg_boost",
    "avg_speed",
    "airborne_percentage",
)

SETTINGS_FORMAT_VERSION = 3

KEYRING_SERVICE = "Demolytics"
KEYRING_BALLCHASING_TOKEN = "ballchasing_api_token"

STANDARD_PLAYLIST_MODES: tuple[str, ...] = ("1v1", "2v2", "3v3")

BALLCHASING_VISIBILITY_CHOICES: tuple[str, ...] = ("public", "unlisted", "private")


@dataclass
class AppSettings:
    websocket_port: int = DEFAULT_PORT
    visible_stats: list[str] = field(default_factory=lambda: list(DEFAULT_STATS_TAB_VISIBLE))
    glance_stats: list[str] = field(default_factory=lambda: list(DEFAULT_GLANCE_STATS))
    comparison_game_mode: str = "1v1"
    settings_format_version: int = SETTINGS_FORMAT_VERSION
    database_path: str | None = None
    install_dir: str | None = None
    ballchasing_auto_upload: bool = True
    ballchasing_token: str = ""
    ballchasing_visibility: str = "private"


def get_app_data_dir() -> Path:
    app_data = os.getenv("APPDATA")
    if app_data:
        return Path(app_data) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def get_default_database_path() -> Path:
    return get_app_data_dir() / "demolytics.db"


def get_settings_path() -> Path:
    return get_app_data_dir() / SETTINGS_FILE_NAME


def get_ballchasing_token_from_keyring() -> str:
    try:
        value = keyring.get_password(KEYRING_SERVICE, KEYRING_BALLCHASING_TOKEN)
    except Exception:
        return ""
    return (value or "").strip()


def sync_ballchasing_token_to_keyring(token: str) -> None:
    token = token.strip()
    try:
        if token:
            keyring.set_password(KEYRING_SERVICE, KEYRING_BALLCHASING_TOKEN, token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_BALLCHASING_TOKEN)
            except keyring_errors.PasswordDeleteError:
                pass
    except Exception:
        pass


def _ballchasing_token_with_legacy_migration(legacy_plaintext: str) -> str:
    stored = get_ballchasing_token_from_keyring()
    if stored:
        return stored
    legacy = legacy_plaintext.strip()
    if not legacy:
        return ""
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_BALLCHASING_TOKEN, legacy)
    except Exception:
        pass
    return legacy


def load_settings(path: Path | None = None) -> AppSettings:
    settings_path = path or get_settings_path()
    if not settings_path.exists():
        settings = AppSettings(database_path=str(get_default_database_path()))
        settings.ballchasing_token = get_ballchasing_token_from_keyring()
        return settings

    raw = json.loads(settings_path.read_text(encoding="utf-8"))
    legacy_token = ""
    if isinstance(raw.get("ballchasing_token"), str):
        legacy_token = raw["ballchasing_token"].strip()
    prev_fmt = int(raw.get("settings_format_version", 1))
    coerced = _coerce_known_settings(raw)
    settings = AppSettings(**coerced)
    settings.ballchasing_token = _ballchasing_token_with_legacy_migration(legacy_token)
    if settings.database_path is None:
        settings.database_path = str(get_default_database_path())
    if prev_fmt < SETTINGS_FORMAT_VERSION:
        save_settings(settings)
    return settings


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    sync_ballchasing_token_to_keyring(settings.ballchasing_token)
    settings_path = path or get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    payload.pop("ballchasing_token", None)
    settings_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _coerce_known_settings(raw: dict[str, Any]) -> dict[str, Any]:
    visible_stats = raw.get("visible_stats")
    if not isinstance(visible_stats, list):
        visible_stats = list(DEFAULT_STATS_TAB_VISIBLE)

    glance_stats = raw.get("glance_stats")
    if not isinstance(glance_stats, list):
        glance_stats = list(DEFAULT_GLANCE_STATS)
    glance_stats = [str(key) for key in glance_stats if str(key) in GLANCE_STAT_KEYS]
    if not glance_stats:
        glance_stats = list(DEFAULT_GLANCE_STATS)
    elif tuple(glance_stats) == _PREVIOUS_DEFAULT_GLANCE_STATS:
        glance_stats = list(DEFAULT_GLANCE_STATS)

    visible_stats = [str(key) for key in visible_stats if str(key) in STATS_TAB_COLUMN_KEYS]
    if not visible_stats:
        visible_stats = list(DEFAULT_STATS_TAB_VISIBLE)

    fmt_version = int(raw.get("settings_format_version", 1))
    if fmt_version < SETTINGS_FORMAT_VERSION and not any(k.startswith("team_") for k in visible_stats):
        visible_stats = list(dict.fromkeys(list(visible_stats) + list(TEAM_STAT_KEYS)))
    fmt_version = SETTINGS_FORMAT_VERSION

    comparison_game_mode = str(raw.get("comparison_game_mode", "1v1"))
    if comparison_game_mode not in STANDARD_PLAYLIST_MODES:
        comparison_game_mode = "1v1"

    ballchasing_auto_upload = bool(raw.get("ballchasing_auto_upload", True))
    ballchasing_visibility = str(raw.get("ballchasing_visibility", "private")).lower()
    if ballchasing_visibility not in BALLCHASING_VISIBILITY_CHOICES:
        ballchasing_visibility = "private"

    return {
        "websocket_port": int(raw.get("websocket_port", DEFAULT_PORT)),
        "visible_stats": visible_stats,
        "glance_stats": glance_stats,
        "comparison_game_mode": comparison_game_mode,
        "settings_format_version": fmt_version,
        "database_path": raw.get("database_path"),
        "install_dir": raw.get("install_dir"),
        "ballchasing_auto_upload": ballchasing_auto_upload,
        "ballchasing_visibility": ballchasing_visibility,
    }
