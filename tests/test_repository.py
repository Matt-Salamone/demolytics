from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from demolytics.db.repository import DemolyticsRepository
from demolytics.domain.aggregator import CompletedMatch, PlayerStatsSnapshot, SessionSnapshot
from demolytics.domain.goal_insights import MIN_HISTORICAL_MATCHES


class RepositoryTests(unittest.TestCase):
    def test_save_match_and_query_averages_and_encounters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="2v2",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                wins=1,
                losses=0,
            )
            repository.upsert_session(session)
            repository.save_completed_match(
                CompletedMatch(
                    match_guid="M1",
                    session_id="S1",
                    timestamp=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    game_mode="2v2",
                    user_result="Win",
                    duration_seconds=300,
                    players=(
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|111|0",
                            player_name="User",
                            team_num=0,
                            is_user=True,
                            stats={"score": 500, "goals": 2, "shots": 4, "avg_boost": 50},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|222|0",
                            player_name="Mate",
                            team_num=0,
                            is_user=False,
                            stats={"score": 250, "goals": 1, "shots": 2, "avg_boost": 40},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Epic|333|0",
                            player_name="Opponent",
                            team_num=1,
                            is_user=False,
                            stats={"score": 300, "goals": 1, "shots": 3, "avg_boost": 35},
                        ),
                    ),
                )
            )
            repository.save_completed_match(
                CompletedMatch(
                    match_guid="M2",
                    session_id="S1",
                    timestamp=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                    game_mode="2v2",
                    user_result="Loss",
                    duration_seconds=300,
                    players=(
                        PlayerStatsSnapshot(
                            match_guid="M2",
                            primary_id="Steam|111|0",
                            player_name="User",
                            team_num=0,
                            is_user=True,
                            stats={"score": 100, "goals": 0, "shots": 1, "avg_boost": 45},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M2",
                            primary_id="Steam|222|0",
                            player_name="Mate",
                            team_num=0,
                            is_user=False,
                            stats={"score": 200, "goals": 1, "shots": 2, "avg_boost": 42},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M2",
                            primary_id="Epic|333|0",
                            player_name="Opponent",
                            team_num=1,
                            is_user=False,
                            stats={"score": 400, "goals": 2, "shots": 4, "avg_boost": 55},
                        ),
                    ),
                )
            )

            matches = repository.list_matches()
            user_averages = repository.get_user_averages(game_mode="2v2")
            baseline = repository.get_global_baseline(game_mode="2v2")
            encounters = repository.list_encounters()

        self.assertEqual(len(matches), 2)
        self.assertEqual(user_averages["score"], 300)
        self.assertAlmostEqual(baseline["goals"], 1.25)
        self.assertEqual(len(encounters), 2)
        teammate = next(row for row in encounters if row["player_name"] == "Mate")
        opponent = next(row for row in encounters if row["player_name"] == "Opponent")
        self.assertEqual(teammate["teammate_games"], 2)
        self.assertEqual(int(teammate["teammate_wins"]), 1)
        self.assertEqual(int(teammate["teammate_losses"]), 1)
        self.assertEqual(opponent["opponent_games"], 2)
        self.assertEqual(int(opponent["opponent_wins"]), 1)
        self.assertEqual(int(opponent["opponent_losses"]), 1)
        self.assertEqual(int(teammate["total_games"]), 2)
        self.assertEqual(int(opponent["total_games"]), 2)

    def test_list_encounters_sort_by_recent_vs_games(self) -> None:
        def match(
            guid: str,
            ts: datetime,
            mate_pid: str,
            mate_name: str,
        ) -> CompletedMatch:
            return CompletedMatch(
                match_guid=guid,
                session_id="S1",
                timestamp=ts,
                game_mode="2v2",
                user_result="Win",
                duration_seconds=300,
                players=(
                    PlayerStatsSnapshot(
                        match_guid=guid,
                        primary_id="Steam|111|0",
                        player_name="User",
                        team_num=0,
                        is_user=True,
                        stats={"score": 100},
                    ),
                    PlayerStatsSnapshot(
                        match_guid=guid,
                        primary_id=mate_pid,
                        player_name=mate_name,
                        team_num=0,
                        is_user=False,
                        stats={"score": 50},
                    ),
                    PlayerStatsSnapshot(
                        match_guid=guid,
                        primary_id="Epic|333|0",
                        player_name="Other",
                        team_num=1,
                        is_user=False,
                        stats={"score": 40},
                    ),
                ),
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            repository.upsert_session(
                SessionSnapshot(
                    session_id="S1",
                    game_mode="2v2",
                    start_time=datetime(2026, 1, 1, tzinfo=UTC),
                    wins=0,
                    losses=0,
                )
            )
            for i in range(5):
                repository.save_completed_match(
                    match(
                        f"M{i}",
                        datetime(2026, 1, 2, i, 0, tzinfo=UTC),
                        "Steam|heavy|0",
                        "Heavy",
                    )
                )
            repository.save_completed_match(
                match(
                    "M_recent",
                    datetime(2026, 1, 10, 0, 0, tzinfo=UTC),
                    "Steam|recent|0",
                    "RecentPal",
                )
            )

            by_recent = repository.list_encounters(sort_by="recent")
            by_games = repository.list_encounters(sort_by="games")

        names_recent = [r["player_name"] for r in by_recent]
        names_games = [r["player_name"] for r in by_games]
        self.assertLess(names_recent.index("RecentPal"), names_recent.index("Heavy"))
        self.assertLess(names_games.index("Heavy"), names_games.index("RecentPal"))
        heavy = next(r for r in by_games if r["player_name"] == "Heavy")
        recent_pal = next(r for r in by_recent if r["player_name"] == "RecentPal")
        self.assertEqual(int(heavy["total_games"]), 5)
        self.assertEqual(int(recent_pal["total_games"]), 1)

    def test_list_encounters_invalid_sort(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            with self.assertRaises(ValueError):
                repository.list_encounters(sort_by="nope")

    def test_get_encounters_for_primary_ids_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="2v2",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                wins=1,
                losses=0,
            )
            repository.upsert_session(session)
            repository.save_completed_match(
                CompletedMatch(
                    match_guid="M1",
                    session_id="S1",
                    timestamp=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    game_mode="2v2",
                    user_result="Win",
                    duration_seconds=300,
                    players=(
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|111|0",
                            player_name="User",
                            team_num=0,
                            is_user=True,
                            stats={"score": 100},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|222|0",
                            player_name="Mate",
                            team_num=0,
                            is_user=False,
                            stats={"score": 50},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Epic|333|0",
                            player_name="Opponent",
                            team_num=1,
                            is_user=False,
                            stats={"score": 40},
                        ),
                    ),
                )
            )
            by_id = repository.get_encounters_for_primary_ids(("Steam|222|0", "Epic|999|0"))
            self.assertIn("Steam|222|0", by_id)
            self.assertEqual(int(by_id["Steam|222|0"]["teammate_games"]), 1)
            self.assertEqual(int(by_id["Steam|222|0"]["opponent_games"]), 0)
            self.assertEqual(int(by_id["Steam|222|0"]["teammate_wins"]), 1)
            self.assertEqual(int(by_id["Steam|222|0"]["teammate_losses"]), 0)
            self.assertNotIn("Epic|999|0", by_id)

    def test_clear_all_data_removes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="2v2",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                wins=1,
                losses=0,
            )
            repository.upsert_session(session)
            repository.clear_all_data()
            self.assertEqual(len(repository.list_matches()), 0)
            self.assertEqual(len(repository.list_encounters()), 0)

    def test_clear_performance_statistics_preserving_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "demolytics.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="2v2",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
                wins=1,
                losses=0,
            )
            repository.upsert_session(session)
            repository.save_completed_match(
                CompletedMatch(
                    match_guid="M1",
                    session_id="S1",
                    timestamp=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    game_mode="2v2",
                    user_result="Win",
                    duration_seconds=300,
                    players=(
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|111|0",
                            player_name="User",
                            team_num=0,
                            is_user=True,
                            stats={"score": 500, "goals": 2, "shots": 4, "avg_boost": 50},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Steam|222|0",
                            player_name="Mate",
                            team_num=0,
                            is_user=False,
                            stats={"score": 250, "goals": 1, "shots": 2, "avg_boost": 40},
                        ),
                        PlayerStatsSnapshot(
                            match_guid="M1",
                            primary_id="Epic|333|0",
                            player_name="Opponent",
                            team_num=1,
                            is_user=False,
                            stats={"score": 300, "goals": 1, "shots": 3, "avg_boost": 35},
                        ),
                    ),
                )
            )

            repository.clear_performance_statistics_preserving_matches()

            user_averages = repository.get_user_averages(game_mode="2v2")
            self.assertEqual(user_averages["score"], 0)
            self.assertEqual(user_averages["goals"], 0)
            encounters = repository.list_encounters()
            self.assertEqual(len(encounters), 2)
            teammate = next(row for row in encounters if row["player_name"] == "Mate")
            self.assertEqual(int(teammate["teammate_wins"]), 1)
            self.assertEqual(int(teammate["teammate_losses"]), 0)
            self.assertEqual(len(repository.list_matches()), 1)

    def test_fetch_rolling_goal_insight_baselines(self) -> None:
        from demolytics.domain.stats import SUPPORTED_STAT_KEYS

        def st(**kw: float) -> dict[str, float]:
            base = {k: 0.0 for k in SUPPORTED_STAT_KEYS}
            base.update(kw)
            return base

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "roll.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="1v1",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
            )
            repository.upsert_session(session)

            def match_1v1(guid: str, minute: int, user_goals: float) -> CompletedMatch:
                return CompletedMatch(
                    match_guid=guid,
                    session_id="S1",
                    timestamp=datetime(2026, 1, 1, minute, 0, tzinfo=UTC),
                    game_mode="1v1",
                    user_result="Win",
                    duration_seconds=120.0,
                    players=(
                        PlayerStatsSnapshot(
                            match_guid=guid,
                            primary_id="Steam|U|0",
                            player_name="User",
                            team_num=0,
                            is_user=True,
                            stats=st(goals=user_goals, shots=4.0, time_zero_boost=20.0),
                        ),
                        PlayerStatsSnapshot(
                            match_guid=guid,
                            primary_id="Epic|O|0",
                            player_name="Opp",
                            team_num=1,
                            is_user=False,
                            stats=st(goals=0.0, shots=2.0, time_zero_boost=30.0),
                        ),
                    ),
                )

            for guid, minute, ug in (
                ("MA", 1, 1.0),
                ("MB", 2, 1.0),
                ("MC", 3, 1.0),
                ("MD", 4, 1.0),
                ("ME", 5, 1.0),
            ):
                repository.save_completed_match(match_1v1(guid, minute, ug))

            hb = repository.fetch_rolling_goal_insight_baselines("Steam|U|0", "1v1")
            self.assertIsNotNone(hb)
            assert hb is not None
            self.assertEqual(hb.n_matches, 5)
            self.assertEqual(hb.n_opponent_samples, 5)
            self.assertAlmostEqual(hb.user_rates["goals"], 0.5, places=5)

            self.assertIsNone(
                repository.fetch_rolling_goal_insight_baselines(
                    "Steam|U|0", "1v1", exclude_match_guid="MA", min_matches=MIN_HISTORICAL_MATCHES
                )
            )
            self.assertIsNone(repository.fetch_rolling_goal_insight_baselines("Steam|U|0", "3v3"))

    def test_fetch_rolling_none_below_default_match_count(self) -> None:
        from demolytics.domain.stats import SUPPORTED_STAT_KEYS

        def st(**kw: float) -> dict[str, float]:
            base = {k: 0.0 for k in SUPPORTED_STAT_KEYS}
            base.update(kw)
            return base

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DemolyticsRepository(Path(temp_dir) / "newuser.db")
            repository.initialize()
            session = SessionSnapshot(
                session_id="S1",
                game_mode="1v1",
                start_time=datetime(2026, 1, 1, tzinfo=UTC),
            )
            repository.upsert_session(session)
            for i, guid in enumerate(("M1", "M2", "M3"), 1):
                repository.save_completed_match(
                    CompletedMatch(
                        match_guid=guid,
                        session_id="S1",
                        timestamp=datetime(2026, 1, 1, i, 0, tzinfo=UTC),
                        game_mode="1v1",
                        user_result="Win",
                        duration_seconds=60.0,
                        players=(
                            PlayerStatsSnapshot(
                                match_guid=guid,
                                primary_id="Steam|U|0",
                                player_name="User",
                                team_num=0,
                                is_user=True,
                                stats=st(goals=1.0, shots=2.0),
                            ),
                            PlayerStatsSnapshot(
                                match_guid=guid,
                                primary_id="Epic|O|0",
                                player_name="Opp",
                                team_num=1,
                                is_user=False,
                                stats=st(goals=0.0, shots=1.0),
                            ),
                        ),
                    )
                )
            self.assertIsNone(repository.fetch_rolling_goal_insight_baselines("Steam|U|0", "1v1"))


if __name__ == "__main__":
    unittest.main()
