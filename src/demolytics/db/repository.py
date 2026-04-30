from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from demolytics.domain.aggregator import (
    CompletedMatch,
    PlayerStatsSnapshot,
    SessionSnapshot,
)
from demolytics.domain.goal_insight_stats import (
    average_normalized_rows,
    normalize_stats_for_goal_insight,
)
from demolytics.domain.goal_insights import HistoricalBaselines, MIN_HISTORICAL_MATCHES
from demolytics.domain.stats import SUPPORTED_STAT_KEYS

STAT_COLUMNS_SQL = ",\n".join(f"{key} REAL DEFAULT 0" for key in SUPPORTED_STAT_KEYS)


def stats_dict_from_player_row(row: sqlite3.Row) -> dict[str, float]:
    return {k: float(row[k] or 0) for k in SUPPORTED_STAT_KEYS}


class DemolyticsRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    start_time TEXT NOT NULL,
                    game_mode TEXT NOT NULL,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    ended_at TEXT
                );

                CREATE TABLE IF NOT EXISTS matches (
                    match_guid TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    inferred_game_mode TEXT NOT NULL,
                    user_result TEXT,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS player_match_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_guid TEXT NOT NULL,
                    primary_id TEXT NOT NULL,
                    player_name TEXT NOT NULL,
                    is_user INTEGER NOT NULL DEFAULT 0,
                    team_num INTEGER NOT NULL,
                    {STAT_COLUMNS_SQL},
                    UNIQUE(match_guid, primary_id),
                    FOREIGN KEY (match_guid) REFERENCES matches(match_guid) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_matches_mode
                    ON matches(inferred_game_mode);
                CREATE INDEX IF NOT EXISTS idx_player_stats_primary_id
                    ON player_match_stats(primary_id);
                CREATE INDEX IF NOT EXISTS idx_player_stats_match_user
                    ON player_match_stats(match_guid, is_user);
                """
            )
            self._migrate_player_match_stat_columns(connection)

    def _migrate_player_match_stat_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(player_match_stats)").fetchall()
        existing = {str(row["name"]) for row in rows}
        for key in SUPPORTED_STAT_KEYS:
            if key in existing:
                continue
            connection.execute(f"ALTER TABLE player_match_stats ADD COLUMN {key} REAL DEFAULT 0")

    def clear_all_data(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM player_match_stats")
            connection.execute("DELETE FROM matches")
            connection.execute("DELETE FROM sessions")

    def clear_performance_statistics_preserving_matches(self) -> None:
        """Zero per-match stat columns and session W/L; keeps matches and encounter history."""
        assignments = ", ".join(f"{key} = 0" for key in SUPPORTED_STAT_KEYS)
        with self.connect() as connection:
            connection.execute(f"UPDATE player_match_stats SET {assignments}")
            connection.execute("UPDATE sessions SET wins = 0, losses = 0")

    def upsert_session(self, session: SessionSnapshot) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (session_id, start_time, game_mode, wins, losses)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    game_mode = excluded.game_mode,
                    wins = excluded.wins,
                    losses = excluded.losses
                """,
                (
                    session.session_id,
                    session.start_time.isoformat(),
                    session.game_mode,
                    session.wins,
                    session.losses,
                ),
            )

    def save_completed_match(self, completed_match: CompletedMatch) -> None:
        if not completed_match.players:
            return

        with self.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO matches (
                    match_guid,
                    session_id,
                    timestamp,
                    inferred_game_mode,
                    user_result,
                    duration_seconds
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_guid) DO UPDATE SET
                    session_id = excluded.session_id,
                    timestamp = excluded.timestamp,
                    inferred_game_mode = excluded.inferred_game_mode,
                    user_result = excluded.user_result,
                    duration_seconds = excluded.duration_seconds
                """,
                (
                    completed_match.match_guid,
                    completed_match.session_id,
                    completed_match.timestamp.isoformat(),
                    completed_match.game_mode,
                    completed_match.user_result,
                    completed_match.duration_seconds,
                ),
            )
            connection.execute(
                "DELETE FROM player_match_stats WHERE match_guid = ?",
                (completed_match.match_guid,),
            )
            connection.executemany(
                _insert_player_sql(),
                [_player_insert_values(player) for player in completed_match.players],
            )

    def list_matches(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        m.match_guid,
                        m.timestamp,
                        m.inferred_game_mode,
                        m.user_result,
                        m.duration_seconds,
                        s.wins,
                        s.losses
                    FROM matches m
                    JOIN sessions s ON s.session_id = m.session_id
                    ORDER BY m.timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def get_match_players(self, match_guid: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT *
                    FROM player_match_stats
                    WHERE match_guid = ?
                    ORDER BY team_num, score DESC, player_name
                    """,
                    (match_guid,),
                )
            )

    def get_user_averages(
        self,
        game_mode: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, float]:
        return self._averages(is_user=True, game_mode=game_mode, session_id=session_id)

    def fetch_rolling_goal_insight_baselines(
        self,
        user_primary_id: str,
        game_mode: str,
        *,
        exclude_match_guid: str | None = None,
        limit: int = 20,
        min_matches: int = MIN_HISTORICAL_MATCHES,
    ) -> HistoricalBaselines | None:
        """Trailing normalized averages for the user and opponents faced (same playlist only)."""
        if not user_primary_id or not game_mode:
            return None

        exclude_sql = ""
        params: list[Any] = [user_primary_id, game_mode]
        if exclude_match_guid:
            exclude_sql = "AND m.match_guid != ?"
            params.append(exclude_match_guid)
        params.append(limit)

        with self.connect() as connection:
            recent = connection.execute(
                f"""
                SELECT m.match_guid, m.duration_seconds
                FROM matches m
                INNER JOIN player_match_stats u
                    ON u.match_guid = m.match_guid
                    AND u.is_user = 1
                    AND u.primary_id = ?
                WHERE m.inferred_game_mode = ?
                {exclude_sql}
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        if len(recent) < min_matches:
            return None

        match_guids = [str(r["match_guid"]) for r in recent]
        durations = {str(r["match_guid"]): float(r["duration_seconds"] or 0) for r in recent}
        placeholders = ", ".join("?" for _ in match_guids)

        with self.connect() as connection:
            all_players = connection.execute(
                f"""
                SELECT p.*
                FROM player_match_stats p
                INNER JOIN matches m ON m.match_guid = p.match_guid
                WHERE p.match_guid IN ({placeholders})
                """,
                match_guids,
            ).fetchall()

        by_match: dict[str, list[sqlite3.Row]] = {}
        for prow in all_players:
            mg = str(prow["match_guid"])
            by_match.setdefault(mg, []).append(prow)

        user_norm_rows: list[dict[str, float]] = []
        opp_norm_rows: list[dict[str, float]] = []

        for mg in match_guids:
            plist = by_match.get(mg, [])
            dur = max(durations.get(mg, 0.0), 1e-3)
            user_row = next(
                (
                    p
                    for p in plist
                    if int(p["is_user"]) == 1 and str(p["primary_id"]) == user_primary_id
                ),
                None,
            )
            if user_row is None:
                continue
            ustats = stats_dict_from_player_row(user_row)
            user_norm_rows.append(normalize_stats_for_goal_insight(ustats, dur))
            ut = int(user_row["team_num"])
            for p in plist:
                if int(p["is_user"]) == 1:
                    continue
                if int(p["team_num"]) == ut:
                    continue
                ost = stats_dict_from_player_row(p)
                opp_norm_rows.append(normalize_stats_for_goal_insight(ost, dur))

        if len(user_norm_rows) < min_matches:
            return None

        return HistoricalBaselines(
            user_rates=average_normalized_rows(user_norm_rows),
            opponent_rates=average_normalized_rows(opp_norm_rows),
            n_matches=len(user_norm_rows),
            n_opponent_samples=len(opp_norm_rows),
        )

    def get_global_baseline(self, game_mode: str | None = None) -> dict[str, float]:
        return self._averages(is_user=False, game_mode=game_mode)

    def list_encounters(self, limit: int = 250, *, sort_by: str = "recent") -> list[sqlite3.Row]:
        order_clause = (
            "ORDER BY last_played_at DESC, player_name ASC"
            if sort_by == "recent"
            else "ORDER BY total_games DESC, player_name ASC"
        )
        if sort_by not in {"recent", "games"}:
            raise ValueError(f"sort_by must be 'recent' or 'games', got {sort_by!r}")
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT
                        p.primary_id,
                        MAX(p.player_name) AS player_name,
                        SUM(CASE WHEN p.team_num = u.team_num THEN 1 ELSE 0 END) AS teammate_games,
                        SUM(CASE WHEN p.team_num != u.team_num THEN 1 ELSE 0 END) AS opponent_games,
                        SUM(
                            CASE WHEN p.team_num = u.team_num AND m.user_result = 'Win'
                            THEN 1 ELSE 0 END
                        ) AS teammate_wins,
                        SUM(
                            CASE WHEN p.team_num = u.team_num AND m.user_result = 'Loss'
                            THEN 1 ELSE 0 END
                        ) AS teammate_losses,
                        SUM(
                            CASE WHEN p.team_num != u.team_num AND m.user_result = 'Win'
                            THEN 1 ELSE 0 END
                        ) AS opponent_wins,
                        SUM(
                            CASE WHEN p.team_num != u.team_num AND m.user_result = 'Loss'
                            THEN 1 ELSE 0 END
                        ) AS opponent_losses,
                        COUNT(*) AS total_games,
                        MAX(m.timestamp) AS last_played_at
                    FROM player_match_stats p
                    JOIN player_match_stats u
                        ON u.match_guid = p.match_guid
                        AND u.is_user = 1
                    JOIN matches m ON m.match_guid = p.match_guid
                    WHERE p.is_user = 0
                    GROUP BY p.primary_id
                    {order_clause}
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def get_encounters_for_primary_ids(self, primary_ids: Iterable[str]) -> dict[str, sqlite3.Row]:
        """Encounter counts and W/L as teammate vs opponent (from saved user_result) per platform ID."""
        ids = tuple({pid for pid in primary_ids if pid})
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    p.primary_id,
                    MAX(p.player_name) AS player_name,
                    SUM(CASE WHEN p.team_num = u.team_num THEN 1 ELSE 0 END) AS teammate_games,
                    SUM(CASE WHEN p.team_num != u.team_num THEN 1 ELSE 0 END) AS opponent_games,
                    SUM(
                        CASE WHEN p.team_num = u.team_num AND m.user_result = 'Win'
                        THEN 1 ELSE 0 END
                    ) AS teammate_wins,
                    SUM(
                        CASE WHEN p.team_num = u.team_num AND m.user_result = 'Loss'
                        THEN 1 ELSE 0 END
                    ) AS teammate_losses,
                    SUM(
                        CASE WHEN p.team_num != u.team_num AND m.user_result = 'Win'
                        THEN 1 ELSE 0 END
                    ) AS opponent_wins,
                    SUM(
                        CASE WHEN p.team_num != u.team_num AND m.user_result = 'Loss'
                        THEN 1 ELSE 0 END
                    ) AS opponent_losses,
                    COUNT(*) AS total_games
                FROM player_match_stats p
                JOIN player_match_stats u
                    ON u.match_guid = p.match_guid
                    AND u.is_user = 1
                JOIN matches m ON m.match_guid = p.match_guid
                WHERE p.is_user = 0 AND p.primary_id IN ({placeholders})
                GROUP BY p.primary_id
                """,
                ids,
            ).fetchall()
        return {str(row["primary_id"]): row for row in rows}

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        return None if row is None else str(row["value"])

    def _averages(
        self,
        is_user: bool,
        game_mode: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, float]:
        stat_select = ", ".join(f"AVG(p.{key}) AS {key}" for key in SUPPORTED_STAT_KEYS)
        clauses = ["p.is_user = ?"]
        values: list[Any] = [1 if is_user else 0]
        if game_mode:
            clauses.append("m.inferred_game_mode = ?")
            values.append(game_mode)
        if session_id:
            clauses.append("m.session_id = ?")
            values.append(session_id)

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT {stat_select}
            FROM player_match_stats p
            JOIN matches m ON m.match_guid = p.match_guid
            WHERE {where_sql}
        """
        with self.connect() as connection:
            row = connection.execute(sql, values).fetchone()
        if row is None:
            return {key: 0.0 for key in SUPPORTED_STAT_KEYS}
        return {key: float(row[key] or 0) for key in SUPPORTED_STAT_KEYS}


def _insert_player_sql() -> str:
    stat_columns = ", ".join(SUPPORTED_STAT_KEYS)
    placeholders = ", ".join("?" for _ in range(5 + len(SUPPORTED_STAT_KEYS)))
    return f"""
        INSERT INTO player_match_stats (
            match_guid,
            primary_id,
            player_name,
            is_user,
            team_num,
            {stat_columns}
        )
        VALUES ({placeholders})
    """


def _player_insert_values(player: PlayerStatsSnapshot) -> tuple[Any, ...]:
    return (
        player.match_guid,
        player.primary_id,
        player.player_name,
        1 if player.is_user else 0,
        player.team_num,
        *[player.stats.get(key, 0.0) for key in SUPPORTED_STAT_KEYS],
    )


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
