from __future__ import annotations

import json
import unittest
from pathlib import Path

from demolytics.domain.events import MatchEndedEvent, StatfeedEvent, UpdateStateEvent, parse_message

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class EventParsingTests(unittest.TestCase):
    def test_parse_update_state(self) -> None:
        raw = (FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8")

        event = parse_message(raw)

        self.assertIsInstance(event, UpdateStateEvent)
        assert isinstance(event, UpdateStateEvent)
        self.assertEqual(event.match_guid, "MATCH-1")
        self.assertEqual(len(event.players), 4)
        self.assertEqual(event.players[0].primary_id, "Steam|111|0")
        self.assertEqual(event.game.target.name, "PlayerA")

    def test_parse_match_ended(self) -> None:
        event = parse_message({"Event": "MatchEnded", "Data": {"MatchGuid": "M", "WinnerTeamNum": 1}})

        self.assertIsInstance(event, MatchEndedEvent)
        assert isinstance(event, MatchEndedEvent)
        self.assertEqual(event.winner_team_num, 1)

    def test_parse_statfeed_event(self) -> None:
        payload = {
            "Event": "StatfeedEvent",
            "Data": {
                "MatchGuid": "M",
                "EventName": "Demolish",
                "Type": "Demolition",
                "MainTarget": {"Name": "A", "Shortcut": 1, "TeamNum": 0},
                "SecondaryTarget": {"Name": "B", "Shortcut": 2, "TeamNum": 1},
            },
        }

        event = parse_message(json.dumps(payload))

        self.assertIsInstance(event, StatfeedEvent)
        assert isinstance(event, StatfeedEvent)
        self.assertEqual(event.stat_type, "Demolition")
        self.assertEqual(event.secondary_target.name, "B")


if __name__ == "__main__":
    unittest.main()
