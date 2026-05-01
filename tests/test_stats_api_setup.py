from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from demolytics.config.rocket_league import DEFAULT_STATS_API_RELATIVE_PATH, parse_stats_api_ini
from demolytics.setup.stats_api import (
    PACKET_SEND_RATE_TARGET,
    _default_stats_api_ini_contents,
    _manual_steps_text,
    _patch_default_stats_api_ini,
    enable_stats_api,
)


class StatsApiSetupTests(unittest.TestCase):
    def test_patch_sets_packet_send_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ini_path = Path(temp_dir) / DEFAULT_STATS_API_RELATIVE_PATH
            ini_path.parent.mkdir(parents=True)

            _patch_default_stats_api_ini(ini_path)

            cfg = parse_stats_api_ini(ini_path)
            self.assertEqual(cfg.packet_send_rate, PACKET_SEND_RATE_TARGET)
            self.assertTrue(cfg.enabled)

    def test_patch_replaces_entire_file_no_duplicate_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ini_path = Path(temp_dir) / DEFAULT_STATS_API_RELATIVE_PATH
            ini_path.parent.mkdir(parents=True)
            ini_path.write_text(
                "[TAGame.MatchStatsExporter_TA]\nPort = 49123\nPacketSendRate = 0\n\n"
                "[StatsAPI]\nPacketSendRate=20\n",
                encoding="utf-8",
            )

            _patch_default_stats_api_ini(ini_path)

            text = ini_path.read_text(encoding="utf-8")
            self.assertIn("[TAGame.MatchStatsExporter_TA]", text)
            self.assertNotIn("[StatsAPI]", text)
            self.assertEqual(text.count("PacketSendRate"), 1)
            cfg = parse_stats_api_ini(ini_path)
            self.assertEqual(cfg.packet_send_rate, float(PACKET_SEND_RATE_TARGET))
            self.assertEqual(cfg.port, 49123)

    def test_manual_steps_include_path_and_rate(self) -> None:
        p = Path(r"C:\Program Files\Rocket League\TAGame\Config\DefaultStatsAPI.ini")
        text = _manual_steps_text(p)
        self.assertIn(str(p.resolve()), text)
        self.assertIn(f"PacketSendRate = {PACKET_SEND_RATE_TARGET}", _default_stats_api_ini_contents())
        self.assertIn(f"PacketSendRate = {PACKET_SEND_RATE_TARGET}", text)

    def test_enable_stats_api_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ok = enable_stats_api(temp_dir, parent=None)
            self.assertTrue(ok)

    @patch("demolytics.setup.stats_api.os.name", "nt")
    @patch("demolytics.setup.stats_api.StatsApiPermissionModal")
    def test_permission_error_shows_modal_windows(
        self,
        modal_cls: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ini_path = Path(temp_dir) / DEFAULT_STATS_API_RELATIVE_PATH
            ini_path.parent.mkdir(parents=True)
            ini_path.write_text("[StatsAPI]\nPacketSendRate=0\n", encoding="utf-8")

            def boom(*_a: object, **_k: object) -> None:
                raise PermissionError(13, "denied")

            with patch(
                "demolytics.setup.stats_api._patch_default_stats_api_ini",
                side_effect=boom,
            ):
                parent = MagicMock()
                ok = enable_stats_api(temp_dir, parent=parent)
                self.assertFalse(ok)
                modal_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
