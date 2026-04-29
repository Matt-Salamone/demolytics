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

    def test_kickoff_countdown_skips_until_round_started(self) -> None:
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
        teams_start = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        teams_after_goal = (TeamState("Blue", 0, 1), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(UpdateStateEvent("M8", (p1, p2), GameState(teams=teams_start, elapsed=0.0)), t0)
        agg.handle_event(
            UpdateStateEvent("M8", (p1, p2), GameState(teams=teams_start, elapsed=5.0)),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        agg.handle_event(
            UpdateStateEvent("M8", (p1, p2), GameState(teams=teams_start, elapsed=10.0)),
            datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        assert agg.current_match is not None
        self.assertAlmostEqual(agg.current_match.duration_seconds, 10.0)
        agg.handle_event(
            UpdateStateEvent(
                "M8",
                (p1, p2),
                GameState(teams=teams_after_goal, elapsed=12.0, replay=True),
            ),
            datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        agg.handle_event(
            UpdateStateEvent(
                "M8",
                (p1, p2),
                GameState(teams=teams_after_goal, elapsed=14.0),
            ),
            datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        )
        self.assertAlmostEqual(agg.current_match.duration_seconds, 10.0)
        agg.handle_event(MatchLifecycleEvent(event_name="CountdownBegin", match_guid="M8"), t0)
        agg.handle_event(
            UpdateStateEvent(
                "M8",
                (p1, p2),
                GameState(teams=teams_after_goal, elapsed=16.0),
            ),
            datetime(2026, 1, 1, 0, 0, 4, tzinfo=UTC),
        )
        self.assertAlmostEqual(agg.current_match.duration_seconds, 10.0)
        agg.handle_event(MatchLifecycleEvent(event_name="RoundStarted", match_guid="M8"), t0)
        agg.handle_event(
            UpdateStateEvent("M8", (p1, p2), GameState(teams=teams_after_goal, elapsed=18.0)),
            datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        )
        self.assertAlmostEqual(agg.current_match.duration_seconds, 12.0)

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

    def test_session_outcome_inferred_when_match_ended_missing(self) -> None:
        """Leaving before the end screen often skips MatchEnded; infer W/L from team scores."""
        aggregator = DemolyticsAggregator()
        payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        aggregator.handle_event(parse_message(payload), datetime(2026, 1, 1, tzinfo=UTC))
        completed = aggregator.handle_event(
            MatchLifecycleEvent(event_name="MatchDestroyed", match_guid="MATCH-1"),
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        ).completed_match
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.user_result, "Win")
        self.assertEqual(aggregator.snapshot().session.wins, 1)
        self.assertEqual(aggregator.snapshot().session.losses, 0)

    def test_session_not_double_counted_match_ended_then_destroy(self) -> None:
        aggregator = DemolyticsAggregator()
        payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        aggregator.handle_event(parse_message(payload), datetime(2026, 1, 1, tzinfo=UTC))
        aggregator.handle_event(
            parse_message({"Event": "MatchEnded", "Data": {"MatchGuid": "MATCH-1", "WinnerTeamNum": 0}})
        )
        aggregator.handle_event(
            MatchLifecycleEvent(event_name="MatchDestroyed", match_guid="MATCH-1"),
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        )
        self.assertEqual(aggregator.snapshot().session.wins, 1)
        self.assertEqual(aggregator.snapshot().session.losses, 0)

    def test_session_outcome_when_switching_match_guid_before_destroy(self) -> None:
        """Going to freeplay/training can change MatchGuid in UpdateState before MatchDestroyed."""
        p1 = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            goals=2,
        )
        p2 = PlayerState(name="B", primary_id="Steam|2|0", shortcut=2, team_num=1)
        teams = (TeamState("Blue", 0, 3), TeamState("Orange", 1, 1))
        agg = DemolyticsAggregator()
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(
            UpdateStateEvent(
                "MATCH-A",
                (p1, p2),
                GameState(
                    teams=teams,
                    elapsed=0.0,
                    target=PlayerRef(name="A", shortcut=1, team_num=0),
                ),
            ),
            t0,
        )
        solo = PlayerState(name="A", primary_id="Steam|1|0", shortcut=1, team_num=0, goals=0)
        agg.handle_event(
            UpdateStateEvent(
                "MATCH-B",
                (solo,),
                GameState(teams=(TeamState("Blue", 0, 0),), elapsed=0.0),
            ),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        self.assertEqual(agg.snapshot().session.wins, 1)
        self.assertEqual(agg.snapshot().live_user_stats.get("goals"), 2.0)

    def test_freeplay_after_ranked_same_match_guid_keeps_scoreboard(self) -> None:
        """RL may keep MatchGuid briefly; freeplay UpdateState must not zero scoreboard stats."""
        p1 = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            goals=3,
            score=100,
        )
        p2 = PlayerState(name="B", primary_id="Steam|2|0", shortcut=2, team_num=1)
        teams = (TeamState("Blue", 0, 1), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        agg.handle_event(
            UpdateStateEvent(
                "SAME-GUID",
                (p1, p2),
                GameState(teams=teams, elapsed=0.0, target=PlayerRef(name="A", shortcut=1, team_num=0)),
            ),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        solo = PlayerState(
            name="A",
            primary_id="Steam|1|0",
            shortcut=1,
            team_num=0,
            goals=0,
            score=0,
        )
        agg.handle_event(
            UpdateStateEvent(
                "SAME-GUID",
                (solo,),
                GameState(teams=(TeamState("Blue", 0, 0),), elapsed=1.0),
            ),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        self.assertEqual(agg.snapshot().live_user_stats.get("goals"), 3.0)
        self.assertEqual(agg.snapshot().live_user_stats.get("score"), 100.0)

    def test_session_persists_when_next_match_loads_with_two_players_first(self) -> None:
        """2v2 often reports two PrimaryIds before the full lobby spawns; avoid 1v1 → 2v2 session churn."""
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        full = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        agg = DemolyticsAggregator()
        r1 = agg.handle_event(parse_message(full), t0)
        self.assertIsNotNone(r1.started_session)
        assert r1.started_session is not None
        sid = r1.started_session.session_id

        agg.handle_event(
            MatchLifecycleEvent(event_name="MatchDestroyed", match_guid="MATCH-1"),
            datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )
        p_a = PlayerState(
            name="A",
            primary_id="Steam|a|0",
            shortcut=1,
            team_num=0,
            speed=1000.0,
            boost=50,
        )
        p_b = PlayerState(
            name="B",
            primary_id="Steam|b|0",
            shortcut=2,
            team_num=1,
            speed=1000.0,
            boost=50,
        )
        teams_0 = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        agg.handle_event(
            UpdateStateEvent(
                "MATCH-NEXT",
                (p_a, p_b),
                GameState(teams=teams_0, elapsed=1.0),
            ),
            datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        )
        s_partial = agg.snapshot().session
        self.assertIsNotNone(s_partial)
        assert s_partial is not None
        self.assertEqual(s_partial.session_id, sid)
        self.assertEqual(s_partial.game_mode, "2v2")

        p_c = PlayerState(
            name="C",
            primary_id="Epic|c|0",
            shortcut=3,
            team_num=0,
            speed=1000.0,
            boost=50,
        )
        p_d = PlayerState(
            name="D",
            primary_id="Epic|d|0",
            shortcut=4,
            team_num=1,
            speed=1000.0,
            boost=50,
        )
        r2 = agg.handle_event(
            UpdateStateEvent(
                "MATCH-NEXT",
                (p_a, p_b, p_c, p_d),
                GameState(teams=teams_0, elapsed=2.0),
            ),
            datetime(2026, 1, 1, 0, 3, tzinfo=UTC),
        )
        self.assertIsNone(r2.started_session)
        s_final = agg.snapshot().session
        self.assertIsNotNone(s_final)
        assert s_final is not None
        self.assertEqual(s_final.session_id, sid)

        aggregator = DemolyticsAggregator()
        payload = json.loads((FIXTURE_DIR / "update_state_2v2.json").read_text(encoding="utf-8"))
        aggregator.handle_event(parse_message(payload), datetime(2026, 1, 1, tzinfo=UTC))
        live_goals = aggregator.snapshot().live_user_stats.get("goals")
        aggregator.handle_event(
            MatchLifecycleEvent(event_name="MatchDestroyed", match_guid="MATCH-1"),
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        )
        frozen = aggregator.snapshot().live_user_stats.get("goals")
        self.assertEqual(frozen, live_goals)
        aggregator.handle_event(
            MatchLifecycleEvent(event_name="MatchInitialized", match_guid="MATCH-2"),
            datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
        )
        self.assertEqual(aggregator.snapshot().live_user_stats.get("goals"), frozen)

    def test_user_inferred_when_only_one_player_has_speed_and_boost(self) -> None:
        """RL often omits car telemetry for remote players; the local client is usually the only fully sampled car."""
        local = PlayerState(
            name="Me",
            primary_id="Steam|9|0",
            shortcut=1,
            team_num=0,
            boost=50,
            speed=1000.0,
            on_ground=True,
        )
        remote = PlayerState(
            name="Other",
            primary_id="Steam|8|0",
            shortcut=2,
            team_num=1,
            boost=None,
            speed=None,
            on_ground=True,
        )
        teams = (TeamState("Blue", 0, 0), TeamState("Orange", 1, 0))
        agg = DemolyticsAggregator()
        agg.handle_event(
            UpdateStateEvent(
                "M1",
                (local, remote),
                GameState(teams=teams, elapsed=0.0, target=None),
            ),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.assertEqual(agg.user_primary_id, "Steam|9|0")

    def test_team_snapshots_sums_and_derived_stats(self) -> None:
        teams = (TeamState("Blue", 0, 2), TeamState("Orange", 1, 1))
        players = (
            PlayerState(
                name="A0",
                primary_id="Steam|a|0",
                shortcut=1,
                team_num=0,
                goals=1,
                shots=4,
                boost=50,
                speed=1000,
                on_ground=True,
                demos=2,
            ),
            PlayerState(
                name="A1",
                primary_id="Steam|b|0",
                shortcut=2,
                team_num=0,
                goals=0,
                shots=2,
                boost=80,
                speed=500,
                on_ground=True,
                demos=1,
            ),
            PlayerState(
                name="O0",
                primary_id="Steam|c|0",
                shortcut=3,
                team_num=1,
                goals=1,
                shots=1,
                boost=20,
                speed=200,
                on_ground=True,
                demos=0,
            ),
            PlayerState(
                name="O1",
                primary_id="Steam|d|0",
                shortcut=4,
                team_num=1,
                goals=0,
                shots=0,
                boost=30,
                speed=200,
                on_ground=True,
                demos=0,
            ),
        )
        agg = DemolyticsAggregator(user_primary_id="Steam|a|0")
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        agg.handle_event(UpdateStateEvent("TM", players, GameState(teams=teams, elapsed=0.0)), t0)
        agg.handle_event(
            UpdateStateEvent("TM", players, GameState(teams=teams, elapsed=10.0)),
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        snap = agg.snapshot()
        self.assertEqual(len(snap.live_teams), 2)
        blue = next(t for t in snap.live_teams if t.team_num == 0)
        orange = next(t for t in snap.live_teams if t.team_num == 1)
        self.assertTrue(blue.is_user_team)
        self.assertFalse(orange.is_user_team)
        self.assertEqual(blue.stats["score"], 2.0)
        self.assertEqual(blue.stats["goals_conceded"], 1.0)
        self.assertEqual(blue.stats["demos_inflicted"], 3.0)
        self.assertEqual(blue.stats["shots"], 6.0)
        self.assertEqual(blue.stats["goals"], 1.0)
        self.assertAlmostEqual(blue.stats["shooting_percentage"], 100.0 / 6.0)
        self.assertAlmostEqual(blue.stats["avg_boost"], 65.0)
        self.assertAlmostEqual(orange.stats["avg_boost"], 25.0)
        self.assertAlmostEqual(orange.stats["shooting_percentage"], 100.0)

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
