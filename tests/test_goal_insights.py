from __future__ import annotations

import unittest

from demolytics.domain.aggregator import PlayerStatsSnapshot, TeamStatsSnapshot
from demolytics.domain.goal_insights import MIN_LOBBY_SECONDS, compute_goal_insight


def _p(
    pid: str,
    name: str,
    team: int,
    *,
    time_zero_boost: float = 10.0,
    demos_inflicted: float = 0.0,
    demos_taken: float = 0.0,
    airborne_percentage: float = 20.0,
    avg_boost: float = 50.0,
    shooting_percentage: float = 25.0,
) -> PlayerStatsSnapshot:
    stats = {
        "score": 100.0,
        "goals": 0.0,
        "assists": 0.0,
        "saves": 0.0,
        "shots": 0.0,
        "touches": 0.0,
        "shooting_percentage": shooting_percentage,
        "goals_conceded": 0.0,
        "demos_inflicted": demos_inflicted,
        "demos_taken": demos_taken,
        "avg_boost": avg_boost,
        "time_zero_boost": time_zero_boost,
        "time_full_boost": 0.0,
        "avg_speed": 1000.0,
        "time_boosting": 0.0,
        "time_supersonic": 0.0,
        "time_powerslide": 0.0,
        "time_on_ground": 100.0,
        "time_airborne": 0.0,
        "airborne_percentage": airborne_percentage,
    }
    return PlayerStatsSnapshot("m", pid, name, team, False, stats)


def _t(
    team_num: int,
    name: str,
    demos: float,
    zero_boost: float,
    shoot: float,
    boost: float,
    *,
    ground: float = 200.0,
    air: float = 50.0,
) -> TeamStatsSnapshot:
    stats = {
        "score": 0.0,
        "goals": 0.0,
        "assists": 0.0,
        "saves": 0.0,
        "shots": 0.0,
        "touches": 0.0,
        "shooting_percentage": shoot,
        "goals_conceded": 0.0,
        "demos_inflicted": demos,
        "demos_taken": 0.0,
        "avg_boost": boost,
        "time_zero_boost": zero_boost,
        "time_full_boost": 0.0,
        "avg_speed": 1000.0,
        "time_boosting": 0.0,
        "time_supersonic": 0.0,
        "time_powerslide": 0.0,
        "time_on_ground": ground,
        "time_airborne": air,
        "airborne_percentage": 0.0,
    }
    return TeamStatsSnapshot(team_num, name, False, stats)


class GoalInsightTests(unittest.TestCase):
    def test_returns_none_when_duration_below_floor(self) -> None:
        players = (_p("1", "A", 0), _p("2", "B", 1))
        teams = (_t(0, "Blue", 0, 0, 0, 50), _t(1, "Orange", 0, 0, 0, 50))
        msg = compute_goal_insight(
            0, players, teams, MIN_LOBBY_SECONDS - 1.0, None, user_team_num=0
        )
        self.assertIsNone(msg)

    def test_returns_none_with_single_player_lobby(self) -> None:
        players = (_p("1", "A", 0),)
        teams = (_t(0, "Blue", 0, 0, 0, 50),)
        msg = compute_goal_insight(0, players, teams, 60.0, None, user_team_num=0)
        self.assertIsNone(msg)

    def test_player_time_zero_boost_outlier_among_teammates(self) -> None:
        players = (
            _p("1", "Quiet", 0, time_zero_boost=10.0),
            _p("2", "Quiet2", 0, time_zero_boost=10.0),
            _p("3", "Quiet3", 1, time_zero_boost=10.0),
            _p("4", "Hungry", 1, time_zero_boost=40.0),
        )
        teams = (_t(0, "Blue", 0, 30, 0, 50), _t(1, "Orange", 0, 40, 0, 50))
        msg = compute_goal_insight(1, players, teams, 20.0, "4", user_team_num=1)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("Hungry", msg)
        self.assertIn("Goal", msg)
        self.assertIn("your team", msg)

    def test_team_demo_disparity_when_telemetry_ok(self) -> None:
        players = (
            _p("1", "A", 0, time_zero_boost=12.0),
            _p("2", "B", 0, time_zero_boost=12.0),
            _p("3", "C", 1, time_zero_boost=12.0),
            _p("4", "D", 1, time_zero_boost=12.0),
        )
        teams = (_t(0, "Blue", 9.0, 40, 25, 50), _t(1, "Orange", 1.0, 40, 25, 50))
        msg = compute_goal_insight(0, players, teams, 30.0, None, user_team_num=0)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("Blue", msg)
        self.assertIn("Orange", msg)

    def test_goal_insight_uses_elapsed_clock_when_stat_time_lags(self) -> None:
        """Stat accumulation pauses around goals; match clock should still unlock early insights."""
        players = (
            _p("1", "A", 0, time_zero_boost=10.0),
            _p("2", "B", 0, time_zero_boost=10.0),
            _p("3", "C", 1, time_zero_boost=10.0),
            _p("4", "D", 1, time_zero_boost=40.0),
        )
        teams = (_t(0, "Blue", 0, 30, 0, 50), _t(1, "Orange", 0, 40, 0, 50))
        msg = compute_goal_insight(1, players, teams, 5.0, None, user_team_num=1)
        self.assertIsNone(msg)
        msg_ok = compute_goal_insight(1, players, teams, 20.0, None, user_team_num=1)
        self.assertIsNotNone(msg_ok)

    def test_1v1_team_insight_when_player_outliers_skipped(self) -> None:
        players = (
            _p("1", "SoloA", 0, time_zero_boost=5.0),
            _p("2", "SoloB", 1, time_zero_boost=30.0),
        )
        teams = (_t(0, "Blue", 1.0, 30, 50, 50), _t(1, "Orange", 6.0, 5, 50, 50))
        msg = compute_goal_insight(0, players, teams, 20.0, None, user_team_num=0)
        self.assertIsNotNone(msg)

    def test_team_demo_outlier_with_minimal_team_movement(self) -> None:
        players = (
            _p("1", "A", 0, time_zero_boost=10.0),
            _p("2", "B", 1, time_zero_boost=10.0),
        )
        teams = (
            _t(0, "Blue", 9.0, 40, 25, 50, ground=1.0, air=0.0),
            _t(1, "Orange", 1.0, 40, 25, 50, ground=1.0, air=0.0),
        )
        msg = compute_goal_insight(0, players, teams, 30.0, None, user_team_num=0)
        self.assertIsNotNone(msg)


if __name__ == "__main__":
    unittest.main()
