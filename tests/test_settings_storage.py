from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from demolytics.settings import (
    DEFAULT_GLANCE_STATS,
    AppSettings,
    SETTINGS_FORMAT_VERSION,
    load_settings,
    save_settings,
)


def test_save_settings_json_omits_ballchasing_token(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(database_path=str(tmp_path / "db.sqlite"))
    settings.ballchasing_token = "secret-token"
    with patch("demolytics.settings.sync_ballchasing_token_to_keyring") as sync:
        save_settings(settings, path=path)
        sync.assert_called_once_with("secret-token")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "ballchasing_token" not in data
    assert data["settings_format_version"] == SETTINGS_FORMAT_VERSION


def test_save_settings_clear_token_calls_sync(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(database_path=str(tmp_path / "db.sqlite"))
    settings.ballchasing_token = ""
    with patch("demolytics.settings.sync_ballchasing_token_to_keyring") as sync:
        save_settings(settings, path=path)
        sync.assert_called_once_with("")
    assert "ballchasing_token" not in json.loads(path.read_text(encoding="utf-8"))


def test_load_settings_migrates_legacy_plaintext_token(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "websocket_port": 49123,
                "visible_stats": [],
                "glance_stats": list(DEFAULT_GLANCE_STATS),
                "comparison_game_mode": "1v1",
                "settings_format_version": 2,
                "database_path": str(tmp_path / "db.sqlite"),
                "install_dir": None,
                "ballchasing_auto_upload": True,
                "ballchasing_token": "legacy-secret",
                "ballchasing_visibility": "private",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with (
        patch("demolytics.settings.get_ballchasing_token_from_keyring", return_value=""),
        patch("demolytics.settings.keyring.set_password") as set_pw,
        patch("demolytics.settings.save_settings") as save,
    ):
        loaded = load_settings(path=path)
        assert loaded.ballchasing_token == "legacy-secret"
        set_pw.assert_called_once_with("Demolytics", "ballchasing_api_token", "legacy-secret")
        save.assert_called_once()


def test_load_settings_prefers_keyring_over_legacy_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "websocket_port": 49123,
                "visible_stats": [],
                "glance_stats": list(DEFAULT_GLANCE_STATS),
                "comparison_game_mode": "1v1",
                "settings_format_version": SETTINGS_FORMAT_VERSION,
                "database_path": str(tmp_path / "db.sqlite"),
                "ballchasing_token": "legacy-secret",
                "ballchasing_visibility": "private",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with patch("demolytics.settings.get_ballchasing_token_from_keyring", return_value="from-keyring"):
        loaded = load_settings(path=path)
        assert loaded.ballchasing_token == "from-keyring"


def test_elevated_executable_and_parameters_unfrozen(tmp_path: Path) -> None:
    import sys

    from demolytics.setup.stats_api import _elevated_executable_and_parameters

    ini = tmp_path / "DefaultStatsAPI.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.touch()

    with patch.object(sys, "frozen", False, create=True):
        exe, params = _elevated_executable_and_parameters(ini)
    assert exe == sys.executable
    assert "--elevated-patch-ini" in params
    assert str(ini.resolve()) in params
    assert "-m" in params
    assert "demolytics.main" in params


def test_elevated_executable_and_parameters_frozen(tmp_path: Path) -> None:
    import sys

    from demolytics.setup.stats_api import _elevated_executable_and_parameters

    ini = tmp_path / "DefaultStatsAPI.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.touch()

    with patch.object(sys, "frozen", True, create=True), patch.object(sys, "argv", ["C:\\Apps\\Demolytics.exe"]):
        exe, params = _elevated_executable_and_parameters(ini)
    assert exe == "C:\\Apps\\Demolytics.exe"
    assert "--elevated-patch-ini" in params
    assert str(ini.resolve()) in params
    assert "demolytics.main" not in params
