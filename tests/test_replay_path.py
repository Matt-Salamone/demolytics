from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from demolytics.integrations.replay_path import (
    path_from_replay_created_data,
    replay_demos_directories,
    resolve_replay_path,
)


class ReplayPathTests(unittest.TestCase):
    def test_replay_demos_directories_end_in_tagame_demos(self) -> None:
        for p in replay_demos_directories():
            self.assertEqual(p.name, "Demos")
            self.assertEqual(p.parent.name, "TAGame")

    def test_path_from_replay_name_in_demos_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            demos = Path(tmp)
            target = demos / "hello.replay"
            target.write_bytes(b"x")
            got = path_from_replay_created_data({"ReplayName": "hello"}, demos_dir=demos)
            self.assertEqual(got, target.resolve())

    def test_path_from_full_replay_file_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            demos = Path(tmp)
            target = demos / "full.replay"
            target.write_bytes(b"y")
            got = path_from_replay_created_data({"ReplayFile": str(target)}, demos_dir=demos)
            self.assertEqual(got, target.resolve())

    def test_resolve_fallback_picks_newest_in_time_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            demos = Path(tmp)
            end = datetime(2026, 6, 15, 14, 30, 0, tzinfo=UTC)
            old = demos / "old.replay"
            new = demos / "new.replay"
            old.write_bytes(b"o")
            new.write_bytes(b"n")
            t_old = (end - timedelta(minutes=5)).timestamp()
            t_new = (end + timedelta(seconds=5)).timestamp()
            os.utime(old, (t_old, t_old))
            os.utime(new, (t_new, t_new))
            got = resolve_replay_path(None, end, demos_dir=demos)
            self.assertEqual(got, new.resolve())

    def test_resolve_prefers_replay_created_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            demos = Path(tmp)
            chosen = demos / "from_payload.replay"
            chosen.write_bytes(b"p")
            other = demos / "other.replay"
            other.write_bytes(b"o")
            end = datetime(2026, 6, 15, 14, 30, 0, tzinfo=UTC)
            t_other = (end + timedelta(seconds=2)).timestamp()
            os.utime(other, (t_other, t_other))
            got = resolve_replay_path({"ReplayName": "from_payload"}, match_end=end, demos_dir=demos)
            self.assertEqual(got, chosen.resolve())


if __name__ == "__main__":
    unittest.main()
