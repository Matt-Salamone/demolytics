from __future__ import annotations

import configparser
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_STATS_API_PORT = 49123
DEFAULT_STATS_API_RELATIVE_PATH = Path("TAGame") / "Config" / "DefaultStatsAPI.ini"
DOCUMENTS_RL_TAGAME_CONFIG = (
    Path.home() / "Documents" / "My Games" / "Rocket League" / "TAGame" / "Config"
)


@dataclass(frozen=True)
class StatsApiIni:
    packet_send_rate: float = 0.0
    port: int = DEFAULT_STATS_API_PORT

    @property
    def enabled(self) -> bool:
        return self.packet_send_rate > 0


@dataclass(frozen=True)
class StatsApiStatus:
    install_dir: Path | None
    ini_path: Path | None
    packet_send_rate: float
    port: int
    enabled: bool
    reason: str

    @property
    def setup_required(self) -> bool:
        return not self.enabled


def _stats_api_ini_layer_paths(
    install_dir: Path,
    *,
    documents_config_dir: Path | None,
) -> list[Path]:
    """UE-style layering: install defaults, then per-user Documents overrides."""
    doc = documents_config_dir if documents_config_dir is not None else DOCUMENTS_RL_TAGAME_CONFIG
    return [
        install_dir / DEFAULT_STATS_API_RELATIVE_PATH,
        doc / "DefaultStatsAPI.ini",
        doc / "StatsAPI.ini",
    ]


def _merge_stats_api_ini_layers(paths: Iterable[Path]) -> tuple[StatsApiIni, list[Path]]:
    values: dict[str, str] = {}
    used: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        used.append(path)
        values.update(_parse_ini_values(path.read_text(encoding="utf-8-sig")))
    if not used:
        return StatsApiIni(), []
    packet_send_rate = _parse_float(values.get("PacketSendRate"), default=0.0)
    port = _parse_int(values.get("Port"), default=DEFAULT_STATS_API_PORT)
    return StatsApiIni(packet_send_rate=packet_send_rate, port=port), used


def check_stats_api_status(
    install_dir: Path | str | None = None,
    *,
    documents_config_dir: Path | None = None,
) -> StatsApiStatus:
    resolved_install_dir = Path(install_dir) if install_dir else find_install_dir()
    if resolved_install_dir is None:
        return StatsApiStatus(
            install_dir=None,
            ini_path=None,
            packet_send_rate=0,
            port=DEFAULT_STATS_API_PORT,
            enabled=False,
            reason="Rocket League install directory could not be detected.",
        )

    layers = _stats_api_ini_layer_paths(
        resolved_install_dir,
        documents_config_dir=documents_config_dir,
    )
    config, used_paths = _merge_stats_api_ini_layers(layers)
    expected_install_ini = resolved_install_dir / DEFAULT_STATS_API_RELATIVE_PATH

    if not used_paths:
        return StatsApiStatus(
            install_dir=resolved_install_dir,
            ini_path=expected_install_ini,
            packet_send_rate=0,
            port=DEFAULT_STATS_API_PORT,
            enabled=False,
            reason="DefaultStatsAPI.ini was not found (install or Documents).",
        )

    primary_ini = used_paths[-1]
    if not config.enabled:
        reason = (
            "PacketSendRate is 0 or missing after merging install and "
            r"Documents\My Games\Rocket League\TAGame\Config overrides, "
            "so the local Stats API is disabled."
        )
    else:
        reason = "Rocket League Stats API is enabled."

    return StatsApiStatus(
        install_dir=resolved_install_dir,
        ini_path=primary_ini,
        packet_send_rate=config.packet_send_rate,
        port=config.port,
        enabled=config.enabled,
        reason=reason,
    )


def parse_stats_api_ini(path: Path) -> StatsApiIni:
    values = _parse_ini_values(path.read_text(encoding="utf-8-sig"))
    packet_send_rate = _parse_float(values.get("PacketSendRate"), default=0.0)
    port = _parse_int(values.get("Port"), default=DEFAULT_STATS_API_PORT)
    return StatsApiIni(packet_send_rate=packet_send_rate, port=port)


def find_install_dir(extra_candidates: Iterable[Path] = ()) -> Path | None:
    for candidate in _install_candidates(extra_candidates):
        if _looks_like_rocket_league_install(candidate):
            return candidate
    return None


def setup_instructions(status: StatsApiStatus) -> list[str]:
    ini_path = status.ini_path or Path("<Rocket League>") / DEFAULT_STATS_API_RELATIVE_PATH
    doc_hint = Path.home() / "Documents" / "My Games" / "Rocket League" / "TAGame" / "Config"
    return [
        f"Open {ini_path} (or an override in {doc_hint}).",
        "Replace or create the file so it contains only a [TAGame.MatchStatsExporter_TA] section "
        f"with Port={status.port or DEFAULT_STATS_API_PORT} and PacketSendRate greater than 0 (e.g. 20).",
        "Remove duplicate PacketSendRate lines or extra [StatsAPI] blocks; a merged value of 0 disables the API.",
        "Save the file and restart Rocket League before reconnecting Demolytics.",
    ]


def _parse_ini_values(raw: str) -> dict[str, str]:
    parser = configparser.ConfigParser()
    values: dict[str, str] = {}

    try:
        parser.read_string(raw)
    except configparser.MissingSectionHeaderError:
        parser.read_string("[StatsAPI]\n" + raw)

    for section in parser.sections():
        for key, value in parser.items(section):
            values[_normalize_key(key)] = value.strip()

    # Preserve values from loose key/value lines if ConfigParser lowercased them.
    for line in raw.splitlines():
        match = re.match(r"^\s*([^#;=\s]+)\s*=\s*(.*?)\s*$", line)
        if match:
            values[_normalize_key(match.group(1))] = match.group(2).strip()

    return values


def _normalize_key(key: str) -> str:
    aliases = {
        "packetsendrate": "PacketSendRate",
        "port": "Port",
    }
    return aliases.get(key.strip().lower(), key.strip())


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _install_candidates(extra_candidates: Iterable[Path]) -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(Path(path) for path in extra_candidates)
    candidates.extend(_registry_candidates())
    candidates.extend(_steam_library_candidates())
    candidates.extend(_epic_manifest_candidates())
    candidates.extend(_standard_path_candidates())
    return _dedupe_existing_parent_paths(candidates)


def _registry_candidates() -> list[Path]:
    if os.name != "nt":
        return []

    try:
        import winreg
    except ImportError:
        return []

    candidates: list[Path] = []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    )
    for hive, key_path, value_name in keys:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        candidates.append(Path(value) / "steamapps" / "common" / "rocketleague")
    return candidates


def _steam_library_candidates() -> list[Path]:
    steam_roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    ]
    candidates: list[Path] = []
    for steam_root in steam_roots:
        candidates.append(steam_root / "steamapps" / "common" / "rocketleague")
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        if not library_file.exists():
            continue
        library_text = library_file.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r'"path"\s+"([^"]+)"', library_text):
            library_path = Path(match.group(1).replace("\\\\", "\\"))
            candidates.append(library_path / "steamapps" / "common" / "rocketleague")
    return candidates


def _epic_manifest_candidates() -> list[Path]:
    manifest_root = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Epic"
        / "EpicGamesLauncher"
        / "Data"
        / "Manifests"
    )
    if not manifest_root.exists():
        return []

    candidates: list[Path] = []
    for manifest in manifest_root.glob("*.item"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        display_name = str(data.get("DisplayName", ""))
        app_name = str(data.get("AppName", ""))
        install_location = data.get("InstallLocation")
        if install_location and (
            "rocket league" in display_name.lower() or app_name.lower() == "sugar"
        ):
            candidates.append(Path(install_location))
    return candidates


def _standard_path_candidates() -> list[Path]:
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    return [
        program_files / "Epic Games" / "rocketleague",
        program_files_x86 / "Epic Games" / "rocketleague",
        program_files_x86 / "Steam" / "steamapps" / "common" / "rocketleague",
        program_files / "Steam" / "steamapps" / "common" / "rocketleague",
    ]


def _looks_like_rocket_league_install(path: Path) -> bool:
    return (path / "TAGame").exists() or (path / DEFAULT_STATS_API_RELATIVE_PATH).exists()


def _dedupe_existing_parent_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped
