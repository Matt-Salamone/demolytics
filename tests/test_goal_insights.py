from __future__ import annotations

import unittest

from demolytics.domain.aggregator import PlayerStatsSnapshot
from demolytics.domain.goal_insights import MIN_LOBBY_SECONDS, compute_goal_insight


def _p(
    pid: str,
    name: str,
    team: int,
    *,
    time_zero_boost: float = 10.0,
    demos_inflicted: float = 0.0,
    demos_taken: float = 0.0,
    time_on_ground: float = 100.0,
    time_airborne: float = 0.0,
    airborne_percentage: float | None = None,
    avg_boost: float = 50.0,
    shooting_percentage: float = 25.0,
    is_user: bool = False,
) -> PlayerStatsSnapshot:
    total_ground_air = time_on_ground + time_airborne
    if airborne_percentage is None:
        ap = (100.0 * time_airborne / total_ground_air) if total_ground_air > 0 else 0.0
    else:
        ap = airborne_percentage
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
        "time_on_ground": time_on_ground,
        "time_airborne": time_airborne,
        "airborne_percentage": ap,
    }
    return PlayerStatsSnapshot("m", pid, name, team, is_user, stats)


class GoalInsightTests(unittest.TestCase):
    def test_returns_none_when_duration_below_floor(self) -> None:
        players = (_p("1", "A", 0, is_user=True), _p("2", "B", 1))
        result = compute_goal_insight(players, MIN_LOBBY_SECONDS - 1.0)
        self.assertIsNone(result)

    def test_returns_none_with_single_player_lobby(self) -> None:
        players = (_p("1", "A", 0, is_user=True),)
        result = compute_goal_insight(players, 60.0)
        self.assertIsNone(result)

    def test_returns_none_when_user_not_identified(self) -> None:
        players = (_p("1", "A", 0), _p("2", "B", 1))
        result = compute_goal_insight(players, 30.0)
        self.assertIsNone(result)

    def test_user_time_zero_boost_outlier_vs_teammate(self) -> None:
        players = (
            _p("1", "Quiet", 0, time_zero_boost=10.0),
            _p("2", "Quiet2", 0, time_zero_boost=10.0),
            _p("3", "Quiet3", 1, time_zero_boost=10.0),
            _p("4", "Hungry", 1, time_zero_boost=40.0, is_user=True),
        )
        result = compute_goal_insight(players, 20.0)
        self.assertIsNotNone(result)
        assert result is not None
        msg = result.message
        self.assertIn("You", msg)
        self.assertNotIn("Hungry", msg)
        self.assertIn("0 boost", msg.lower())

    def test_user_avg_boost_below_teammates(self) -> None:
        players = (
            _p("1", "A", 0, avg_boost=50.0),
            _p("2", "B", 0, avg_boost=50.0),
            _p("3", "Me", 0, avg_boost=30.0, is_user=True),
        )
        result = compute_goal_insight(players, 30.0)
        self.assertIsNotNone(result)
        assert result is not None
        msg = result.message
        self.assertIn("You", msg)
        self.assertIn("average boost", msg.lower())

    def test_goal_insight_uses_elapsed_clock_when_stat_time_lags(self) -> None:
        """Stat accumulation pauses around goals; match clock should still unlock early insights."""
        players = (
            _p("1", "A", 0, time_zero_boost=10.0),
            _p("2", "B", 0, time_zero_boost=10.0),
            _p("3", "C", 1, time_zero_boost=10.0),
            _p("4", "D", 1, time_zero_boost=40.0, is_user=True),
        )
        result = compute_goal_insight(players, 3.0)
        self.assertIsNone(result)
        result_ok = compute_goal_insight(players, 5.0)
        self.assertIsNotNone(result_ok)

    def test_1v1_user_demo_below_opponent(self) -> None:
        players = (
            _p("1", "SoloA", 0, demos_inflicted=1.0, is_user=True),
            _p("2", "SoloB", 1, demos_inflicted=6.0),
        )
        result = compute_goal_insight(players, 20.0)
        self.assertIsNotNone(result)
        assert result is not None
        msg = result.message
        self.assertIn("You", msg)
        self.assertIn("opponent", msg.lower())

    def test_1v1_user_demo_above_opponent(self) -> None:
        players = (
            _p("1", "A", 0, demos_inflicted=9.0, is_user=True),
            _p("2", "B", 1, demos_inflicted=1.0),
        )
        result = compute_goal_insight(players, 30.0)
        self.assertIsNotNone(result)
        assert result is not None
        msg = result.message
        self.assertIn("demos inflicted", msg.lower())

    def test_fallback_when_no_outlier(self) -> None:
        players = (
            _p("1", "A", 0),
            _p("2", "B", 0),
            _p("3", "Me", 0, is_user=True),
        )
        result = compute_goal_insight(players, 30.0, insight_salt=0)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.message.startswith("Your "))

    def test_skips_airborne_when_opponent_has_no_ground_time(self) -> None:
        """Other cars often lack on-ground in Bakkes/RL plugin; bogus 100% airborne must not drive insights."""
        players = (
            _p(
                "1",
                "Me",
                0,
                is_user=True,
                time_on_ground=100.0,
                time_airborne=25.0,
            ),
            _p(
                "2",
                "Opp",
                1,
                time_on_ground=0.0,
                time_airborne=200.0,
                airborne_percentage=100.0,
            ),
        )
        result = compute_goal_insight(players, 30.0, insight_salt=0)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotIn("airborne", result.message.lower())

    def test_airborne_outlier_still_works_when_ground_visible_for_all(self) -> None:
        players = (
            _p(
                "1",
                "Me",
                0,
                is_user=True,
                time_on_ground=100.0,
                time_airborne=10.0,
            ),
            _p(
                "2",
                "Opp",
                1,
                time_on_ground=50.0,
                time_airborne=150.0,
                airborne_percentage=75.0,
            ),
        )
        result = compute_goal_insight(players, 30.0)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("airborne", result.message.lower())


if __name__ == "__main__":
    unittest.main()
