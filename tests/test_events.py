from __future__ import annotations

import json
import unittest
from pathlib import Path

from demolytics.domain.events import (
    MatchEndedEvent,
    MatchLifecycleEvent,
    StatfeedEvent,
    UpdateStateEvent,
    _optional_boost_percent,
    parse_message,
)

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

    def test_parse_update_state_when_data_is_json_string(self) -> None:
        """Live Stats API wraps UpdateState.Data as an escaped JSON string (see debug logs)."""
        inner = {
            "MatchGuid": "MG-STRING",
            "Players": [
                {
                    "Name": "Bot",
                    "PrimaryId": "Steam|9|0",
                    "Shortcut": 1,
                    "TeamNum": 0,
                    "Score": 0,
                    "Goals": 0,
                    "Shots": 0,
                    "Assists": 0,
                    "Saves": 0,
                    "Touches": 0,
                    "CarTouches": 0,
                    "Demos": 0,
                }
            ],
            "Game": {
                "Teams": [
                    {"Name": "Blue", "TeamNum": 0, "Score": 0},
                    {"Name": "Orange", "TeamNum": 1, "Score": 0},
                ]
            },
        }
        envelope = {"Event": "UpdateState", "Data": json.dumps(inner)}
        event = parse_message(json.dumps(envelope))

        self.assertIsInstance(event, UpdateStateEvent)
        assert isinstance(event, UpdateStateEvent)
        self.assertEqual(event.match_guid, "MG-STRING")
        self.assertEqual(len(event.players), 1)
        self.assertEqual(event.players[0].name, "Bot")

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

    def test_parse_replay_created_preserves_data(self) -> None:
        payload = {
            "Event": "ReplayCreated",
            "Data": {
                "MatchGuid": "MG-REPLAY",
                "ReplayName": "MyReplay",
                "Extra": 1,
            },
        }
        event = parse_message(json.dumps(payload))
        self.assertIsInstance(event, MatchLifecycleEvent)
        assert isinstance(event, MatchLifecycleEvent)
        self.assertEqual(event.match_guid, "MG-REPLAY")
        self.assertEqual(event.data.get("ReplayName"), "MyReplay")
        self.assertEqual(event.data.get("Extra"), 1)

    def test_optional_boost_percent_integer_vs_normalized_float(self) -> None:
        self.assertEqual(_optional_boost_percent(0), 0)
        self.assertEqual(_optional_boost_percent(1), 1)
        self.assertEqual(_optional_boost_percent(45), 45)
        self.assertEqual(_optional_boost_percent(0.45), 45)
        self.assertEqual(_optional_boost_percent(1.0), 100)
        self.assertEqual(_optional_boost_percent(45.2), 45)

    def test_boost_omitted_implies_zero_when_speed_present(self) -> None:
        """Stats API often drops Boost at empty; omitting Boost while Speed exists implies 0%."""
        payload = {
            "Event": "UpdateState",
            "Data": {
                "MatchGuid": "MG",
                "Players": [
                    {
                        "Name": "Local",
                        "PrimaryId": "Steam|1|0",
                        "Shortcut": 1,
                        "TeamNum": 0,
                        "Score": 0,
                        "Goals": 0,
                        "Shots": 0,
                        "Assists": 0,
                        "Saves": 0,
                        "Touches": 0,
                        "CarTouches": 0,
                        "Demos": 0,
                        "Speed": 1200,
                    },
                ],
                "Game": {
                    "Teams": [
                        {"Name": "Blue", "TeamNum": 0, "Score": 0},
                        {"Name": "Orange", "TeamNum": 1, "Score": 0},
                    ],
                    "Elapsed": 0,
                },
            },
        }
        event = parse_message(payload)
        self.assertIsInstance(event, UpdateStateEvent)
        assert isinstance(event, UpdateStateEvent)
        self.assertEqual(event.players[0].boost, 0)

    def test_boost_explicit_null_stays_unknown(self) -> None:
        payload = {
            "Event": "UpdateState",
            "Data": {
                "MatchGuid": "MG",
                "Players": [
                    {
                        "Name": "Local",
                        "PrimaryId": "Steam|1|0",
                        "Shortcut": 1,
                        "TeamNum": 0,
                        "Score": 0,
                        "Goals": 0,
                        "Shots": 0,
                        "Assists": 0,
                        "Saves": 0,
                        "Touches": 0,
                        "CarTouches": 0,
                        "Demos": 0,
                        "Speed": 1200,
                        "Boost": None,
                    },
                ],
                "Game": {
                    "Teams": [
                        {"Name": "Blue", "TeamNum": 0, "Score": 0},
                        {"Name": "Orange", "TeamNum": 1, "Score": 0},
                    ],
                    "Elapsed": 0,
                },
            },
        }
        event = parse_message(payload)
        assert isinstance(event, UpdateStateEvent)
        self.assertIsNone(event.players[0].boost)


if __name__ == "__main__":
    unittest.main()
