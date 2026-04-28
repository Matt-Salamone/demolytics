from __future__ import annotations

import copy
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from demolytics.domain.aggregator import DemolyticsAggregator, infer_game_mode
from demolytics.domain.events import MatchLifecycleEvent, PlayerState, parse_message

FIXTURE_DIR = Path(__file__).parent / "fixtures"


class AggregatorTests(unittest.TestCase):
    def test_infer_game_mode_from_distinct_players(self) -> None:
        players = tuple(
            PlayerState(
                name=f"Player {index}",
                primary_id=f"Steam|{index}|0",
                shortcut=index,
                team_num=0 if index < 2 else 1,
            )
            for index in range(4)
        )

        self.assertEqual(infer_game_mode(players), "2v2")

    def test_session_match_and_weighted_live_stats(self) -> None:
        aggregator = DemolyticsAggregator()
        first_payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        second_payload = copy.deepcopy(first_payload)
        second_payload["Data"]["Game"]["Elapsed"] = 12
        second_payload["Data"]["Players"][0]["Boost"] = 25
        second_payload["Data"]["Players"][0]["Speed"] = 1300

        first_event = parse_message(first_payload)
        second_event = parse_message(second_payload)

        first_result = aggregator.handle_event(first_event, datetime(2026, 1, 1, tzinfo=UTC))
        second_result = aggregator.handle_event(second_event, datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))

        self.assertIsNotNone(first_result.started_session)
        self.assertIsNone(second_result.started_session)
        self.assertEqual(second_result.snapshot.current_game_mode, "2v2")
        self.assertEqual(second_result.snapshot.user_primary_id, "Steam|111|0")
        self.assertEqual(second_result.snapshot.live_user_stats["avg_boost"], 25)
        self.assertEqual(second_result.snapshot.live_user_stats["avg_speed"], 1300)
        self.assertEqual(second_result.snapshot.live_user_stats["time_boosting"], 2)

    def test_match_completion_includes_user_result(self) -> None:
        aggregator = DemolyticsAggregator()
        payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        first_event = parse_message(payload)
        second_payload = copy.deepcopy(payload)
        second_payload["Data"]["Game"]["Elapsed"] = 12
        second_event = parse_message(second_payload)

        aggregator.handle_event(first_event, datetime(2026, 1, 1, tzinfo=UTC))
        aggregator.handle_event(second_event, datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
        result = aggregator.handle_event(
            parse_message({"Event": "MatchEnded", "Data": {"MatchGuid": "MATCH-1", "WinnerTeamNum": 0}})
        )

        self.assertEqual(result.snapshot.session.wins, 1)

        completed = aggregator.handle_event(
            MatchLifecycleEvent(event_name="MatchDestroyed", match_guid="MATCH-1"),
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        ).completed_match

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.user_result, "Win")
        self.assertEqual(completed.game_mode, "2v2")
        self.assertEqual(len(completed.players), 4)


if __name__ == "__main__":
    unittest.main()
