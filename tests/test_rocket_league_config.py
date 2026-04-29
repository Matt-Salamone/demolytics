from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from demolytics.config.rocket_league import (
    DEFAULT_STATS_API_RELATIVE_PATH,
    check_stats_api_status,
    parse_stats_api_ini,
)


class RocketLeagueConfigTests(unittest.TestCase):
    def test_parse_stats_api_ini_with_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DefaultStatsAPI.ini"
            path.write_text("[StatsAPI]\nPacketSendRate=20\nPort=49200\n", encoding="utf-8")

            config = parse_stats_api_ini(path)

        self.assertTrue(config.enabled)
        self.assertEqual(config.packet_send_rate, 20)
        self.assertEqual(config.port, 49200)

    def test_parse_stats_api_ini_without_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "DefaultStatsAPI.ini"
            path.write_text("PacketSendRate=0\n", encoding="utf-8")

            config = parse_stats_api_ini(path)

        self.assertFalse(config.enabled)
        self.assertEqual(config.port, 49123)

    def test_check_status_reports_missing_ini(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir)
            (install_dir / "TAGame" / "Config").mkdir(parents=True)

            status = check_stats_api_status(install_dir)

        self.assertTrue(status.setup_required)
        self.assertIn("not found", status.reason)

    def test_check_status_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir)
            ini_path = install_dir / DEFAULT_STATS_API_RELATIVE_PATH
            ini_path.parent.mkdir(parents=True)
            ini_path.write_text("PacketSendRate=30\nPort=49124\n", encoding="utf-8")

            status = check_stats_api_status(install_dir)

        self.assertFalse(status.setup_required)
        self.assertEqual(status.port, 49124)

    def test_check_status_documents_override_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir)
            ini_path = install_dir / DEFAULT_STATS_API_RELATIVE_PATH
            ini_path.parent.mkdir(parents=True)
            ini_path.write_text("PacketSendRate=20\nPort=49123\n", encoding="utf-8")

            doc_root = Path(temp_dir) / "doc_cfg"
            doc_cfg = doc_root / "Rocket League" / "TAGame" / "Config"
            doc_cfg.mkdir(parents=True)
            (doc_cfg / "StatsAPI.ini").write_text("Port=49199\n", encoding="utf-8")

            status = check_stats_api_status(install_dir, documents_config_dir=doc_cfg)

        self.assertFalse(status.setup_required)
        self.assertEqual(status.port, 49199)
        self.assertEqual(status.ini_path, doc_cfg / "StatsAPI.ini")


if __name__ == "__main__":
    unittest.main()
