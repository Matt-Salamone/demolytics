from __future__ import annotations

import copy
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from demolytics.domain.aggregator import (
    DemolyticsAggregator,
    infer_game_mode,
    is_freeplay,
)
from demolytics.domain.events import (
    GameState,
    MatchEndedEvent,
    MatchLifecycleEvent,
    PlayerRef,
    PlayerState,
    TeamState,
    UpdateStateEvent,
    parse_message,
)

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

    def test_is_freeplay_single_primary_id(self) -> None:
        solo = PlayerState(
            name="Solo",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
        )
        self.assertTrue(is_freeplay((solo,)))
        duo = (
            PlayerState(name="A", primary_id="Steam|1|0", shortcut=1, team_num=0),
            PlayerState(name="B", primary_id="Steam|2|0", shortcut=2, team_num=1),
        )
        self.assertFalse(is_freeplay(duo))

    def test_freeplay_skips_stat_accumulation(self) -> None:
        solo = PlayerState(
            name="Solo",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            speed=1000.0,
            boost=50,
            boosting=True,
            on_ground=True,
        )
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(
            UpdateStateEvent(
                "FP",
                (solo,),
                GameState(teams=(TeamState("Blue", 0, 0),), elapsed=0.0),
            ),
            t0,
        )
        agg.handle_event(
            UpdateStateEvent(
                "FP",
                (solo,),
                GameState(teams=(TeamState("Blue", 0, 0),), elapsed=30.0),
            ),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        assert agg.current_match is not None
        self.assertAlmostEqual(agg.current_match.duration_seconds, 0.0)
        self.assertEqual(agg.current_match.players["Steam|1|0"].time_boosting, 0.0)

    def test_goal_celebration_window_skips_duration(self) -> None:
        p1 = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            speed=500.0,
            boost=50,
        )
        p2 = PlayerState(
            name="B",
            primary_id="Steam|2|0",
            shortcut=2,
            team_num=1,
            speed=500.0,
            boost=50,
        )
        teams = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(
            UpdateStateEvent(
                "M9",
                (p1, p2),
                GameState(
                    teams=teams,
                    elapsed=0.0,
                    target=PlayerRef(name="A", shortcut=1, team_num=0),
                ),
            ),
            t0,
        )
        agg.handle_event(
            UpdateStateEvent(
                "M9",
                (p1, p2),
                GameState(teams=teams, elapsed=10.0, target=PlayerRef(name="A", shortcut=1, team_num=0)),
            ),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        assert agg.current_match is not None
        self.assertAlmostEqual(agg.current_match.duration_seconds, 5.0)
        agg.handle_event(
            UpdateStateEvent(
                "M9",
                (p1, p2),
                GameState(
                    teams=(TeamState("Blue", 0, 1), TeamState("Orange", 1, 0)),
                    elapsed=90.0,
                    has_winner=True,
                    target=PlayerRef(name="A", shortcut=1, team_num=0),
                ),
            ),
            datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        self.assertAlmostEqual(agg.current_match.duration_seconds, 5.0)

    def test_countdown_resumes_stat_tracking(self) -> None:
        p1 = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            speed=500.0,
            boost=50,
        )
        p2 = PlayerState(
            name="B",
            primary_id="Steam|2|0",
            shortcut=2,
            team_num=1,
            speed=500.0,
            boost=50,
        )
        teams = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(UpdateStateEvent("M8", (p1, p2), GameState(teams=teams, elapsed=0.0)), t0)
        agg.handle_event(
            UpdateStateEvent("M8", (p1, p2), GameState(teams=teams, elapsed=10.0)),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        agg.handle_event(
            UpdateStateEvent(
                "M8",
                (p1, p2),
                GameState(teams=teams, elapsed=20.0, has_winner=True),
            ),
            datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        agg.handle_event(MatchLifecycleEvent(event_name="CountdownBegin", match_guid="M8"), t0)
        agg.handle_event(
            UpdateStateEvent("M8", (p1, p2), GameState(teams=teams, elapsed=25.0)),
            datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        )
        assert agg.current_match is not None
        self.assertAlmostEqual(agg.current_match.duration_seconds, 10.0)

    def test_airborne_counts_when_on_ground_none_in_ranked_match(self) -> None:
        p1 = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            has_car=True,
            on_ground=None,
            speed=1000.0,
            boost=50,
        )
        p2 = PlayerState(
            name="B",
            primary_id="Steam|2|0",
            shortcut=2,
            team_num=1,
            has_car=True,
            on_ground=True,
            speed=800.0,
            boost=40,
        )
        teams = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(
            UpdateStateEvent(
                "M7",
                (p1, p2),
                GameState(
                    teams=teams,
                    elapsed=0.0,
                    target=PlayerRef(name="A", shortcut=1, team_num=0),
                ),
            ),
            t0,
        )
        agg.handle_event(
            UpdateStateEvent(
                "M7",
                (p1, p2),
                GameState(teams=teams, elapsed=4.0, target=PlayerRef(name="A", shortcut=1, team_num=0)),
            ),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        assert agg.current_match is not None
        user = agg.current_match.players["Steam|1|0"]
        self.assertAlmostEqual(user.time_airborne, 4.0)
        snap = user.snapshot(opposing_score=0, is_user=True)
        self.assertGreater(snap.stats["airborne_percentage"], 0.0)

    def test_win_streak_on_session(self) -> None:
        aggregator = DemolyticsAggregator()
        payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        first_event = parse_message(payload)
        second_payload = copy.deepcopy(payload)
        second_payload["Data"]["Game"]["Elapsed"] = 12
        second_event = parse_message(second_payload)

        aggregator.handle_event(first_event, datetime(2026, 1, 1, tzinfo=UTC))
        aggregator.handle_event(second_event, datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC))
        win = aggregator.handle_event(
            MatchEndedEvent(match_guid="MATCH-1", winner_team_num=0),
        )
        self.assertEqual(win.snapshot.session.win_streak, 1)


if __name__ == "__main__":
    unittest.main()
