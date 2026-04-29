from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _windows_personal_folder() -> Path | None:
    """Actual user Documents folder (follows OneDrive / known-folder redirects)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL (5) = My Documents; SHGFP_TYPE_CURRENT (0)
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)
        # S_OK (0) or S_FALSE (1) both mean the path was written; FAILED(hr) iff hr < 0
        if hr < 0 or not buf.value:
            return None
        return Path(buf.value)
    except Exception:
        return None


def replay_demos_directories() -> tuple[Path, ...]:
    """
    Possible Rocket League Demos folders (Windows may use OneDrive-backed Documents).

    Order: shell-resolved Documents, then OneDrive env roots, then home/Documents.
    """
    tail = Path("My Games") / "Rocket League" / "TAGame" / "Demos"
    raw: list[Path] = []

    if sys.platform == "win32":
        win_docs = _windows_personal_folder()
        if win_docs is not None:
            raw.append(win_docs / tail)
        for key in ("OneDrive", "OneDriveConsumer"):
            root = os.environ.get(key, "").strip()
            if root:
                raw.append(Path(root) / "Documents" / tail)

    raw.append(Path.home() / "Documents" / tail)

    seen: set[Path] = set()
    out: list[Path] = []
    for p in raw:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return tuple(out)


def default_replays_folder() -> Path:
    """Primary Demos folder (first candidate)."""
    dirs = replay_demos_directories()
    return dirs[0] if dirs else Path.home() / "Documents" / "My Games" / "Rocket League" / "TAGame" / "Demos"


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
    dirs = (demos_dir,) if demos_dir is not None else replay_demos_directories()
    for demos in dirs:
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
    with mtime within a short window of match end. Searches all known Demos roots.
    """
    dirs = (demos_dir,) if demos_dir is not None else replay_demos_directories()

    if replay_created_data:
        found = path_from_replay_created_data(replay_created_data, demos_dir=demos_dir)
        if found is not None:
            return found

    end_utc = _as_utc(match_end)
    low = end_utc - timedelta(seconds=30)
    # Manual save after the victory screen can land several minutes after MatchDestroyed.
    high = end_utc + timedelta(minutes=15)

    best: Path | None = None
    best_ts: float = -1.0
    for demos in dirs:
        if not demos.is_dir():
            continue
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
