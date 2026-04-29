from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def default_replays_folder() -> Path:
    """Rocket League default replay folder (Windows layout)."""
    return Path.home() / "Documents" / "My Games" / "Rocket League" / "TAGame" / "Demos"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def path_from_replay_created_data(
    data: dict[str, Any],
    *,
    demos_dir: Path | None = None,
) -> Path | None:
    """If the Stats API names a replay file, return it when it exists."""
    demos = demos_dir or default_replays_folder()
    for key in ("ReplayFile", "FilePath", "ReplayName"):
        v = data.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        s = v.strip()
        p = Path(s)
        if p.suffix.lower() == ".replay":
            try:
                if p.is_file():
                    return p.resolve()
            except OSError:
                pass
            if not p.is_absolute():
                try:
                    q = demos / p.name
                    if q.is_file():
                        return q.resolve()
                except OSError:
                    pass
        else:
            try:
                q = demos / f"{s}.replay"
                if q.is_file():
                    return q.resolve()
            except OSError:
                pass
    return None


def resolve_replay_path(
    replay_created_data: dict[str, Any] | None,
    match_end: datetime,
    *,
    demos_dir: Path | None = None,
) -> Path | None:
    """
    Prefer a path from ReplayCreated data; otherwise the newest .replay in Demos
    with mtime within a short window of match end.
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
