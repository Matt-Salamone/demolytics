from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def default_replays_folder() -> Path:
    """Rocket League default auto-replay directory (Windows layout)."""
    return Path.home() / "Documents" / "My Games" / "Rocket League" / "TAGame" / "Demos"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _candidate_paths_from_data(data: dict[str, Any], demos_dir: Path) -> list[Path]:
    """Collect possible replay paths from Stats API ReplayCreated-style payloads."""
    out: list[Path] = []
    for key in ("ReplayFile", "FilePath", "ReplayPath", "FullReplayPath", "Path"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            out.append(Path(v.strip()))
    for key in ("ReplayName", "ReplayFilename", "Name"):
        v = data.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        name = v.strip()
        p = Path(name)
        if p.suffix.lower() == ".replay" and (p.is_absolute() or "/" in name or "\\" in name):
            out.append(p)
        else:
            stem = name[:-7] if name.lower().endswith(".replay") else name
            out.append(demos_dir / f"{stem}.replay")
    for v in data.values():
        if isinstance(v, str) and v.lower().endswith(".replay"):
            out.append(Path(v.strip()))
    return out


def path_from_replay_created_data(
    data: dict[str, Any],
    *,
    demos_dir: Path | None = None,
) -> Path | None:
    demos = demos_dir or default_replays_folder()
    for candidate in _candidate_paths_from_data(data, demos):
        try:
            if candidate.is_file() and candidate.suffix.lower() == ".replay":
                return candidate.resolve()
        except OSError:
            continue
        if not candidate.is_absolute():
            try:
                alt = demos / candidate.name
                if alt.is_file():
                    return alt.resolve()
            except OSError:
                continue
    return None


def resolve_replay_path(
    replay_created_data: dict[str, Any] | None,
    match_end: datetime,
    *,
    demos_dir: Path | None = None,
) -> Path | None:
    """
    Resolve a .replay file: prefer ReplayCreated payload, else newest file in Demos
    whose mtime is within a short window of match end.
    """
    demos = demos_dir or default_replays_folder()
    if replay_created_data:
        found = path_from_replay_created_data(replay_created_data, demos_dir=demos)
        if found is not None:
            return found

    if not demos.is_dir():
        return None

    end_utc = _as_utc(match_end)
    low = end_utc - timedelta(seconds=30)
    high = end_utc + timedelta(minutes=3)

    best: Path | None = None
    best_ts: float = -1.0
    for f in demos.glob("*.replay"):
        try:
            st = f.stat()
        except OSError:
            continue
        mtime_utc = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        if low <= mtime_utc <= high and st.st_mtime > best_ts:
            best = f
            best_ts = st.st_mtime

    return best.resolve() if best is not None else None
