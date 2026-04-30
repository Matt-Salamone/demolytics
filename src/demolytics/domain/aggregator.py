from __future__ import annotations

import logging
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from demolytics.domain.events import (
    GameState,
    MatchEndedEvent,
    MatchLifecycleEvent,
    PlayerRef,
    PlayerState,
    StatfeedEvent,
    StatsEvent,
    TeamState,
    UpdateStateEvent,
)
from demolytics.domain.goal_insights import compute_goal_insight
from demolytics.goal_insight_logging import append_goal_insight_log

LOGGER = logging.getLogger(__name__)

SUPPORTED_MODES = {"1v1", "2v2", "3v3"}

# While loading into 2v2, only 1–2 players with IDs may be visible; defer "1v1" so the session
# is not torn down and recreated when the full lobby (4) appears a moment later.
MODE_SESSION_LOADING_SECONDS = 5.0


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    game_mode: str
    start_time: datetime
    wins: int = 0
    losses: int = 0
    win_streak: int = 0


@dataclass(frozen=True)
class PlayerStatsSnapshot:
    match_guid: str
    primary_id: str
    player_name: str
    team_num: int
    is_user: bool
    stats: dict[str, float]


@dataclass(frozen=True)
class TeamStatsSnapshot:
    team_num: int
    team_name: str
    is_user_team: bool
    stats: dict[str, float]


@dataclass(frozen=True)
class CompletedMatch:
    match_guid: str
    session_id: str
    timestamp: datetime
    game_mode: str
    user_result: str | None
    duration_seconds: float
    players: tuple[PlayerStatsSnapshot, ...]


@dataclass(frozen=True)
class DashboardSnapshot:
    session: SessionSnapshot | None
    current_match_guid: str | None
    current_game_mode: str
    live_user_stats: dict[str, float]
    user_team_stats: dict[str, float]
    live_players: tuple[PlayerStatsSnapshot, ...]
    live_teams: tuple[TeamStatsSnapshot, ...]
    goal_insight: str | None
    user_primary_id: str | None
    status: str
    win_streak: int = 0


@dataclass(frozen=True)
class AggregationResult:
    snapshot: DashboardSnapshot
    started_session: SessionSnapshot | None = None
    completed_match: CompletedMatch | None = None


@dataclass
class PlayerAccumulator:
    match_guid: str
    primary_id: str
    player_name: str
    team_num: int
    shortcut: int | None = None
    score: int = 0
    goals: int = 0
    assists: int = 0
    saves: int = 0
    shots: int = 0
    touches: int = 0
    possession_seconds: float = 0.0
    demos_inflicted_scoreboard: int = 0
    demos_inflicted_statfeed: int = 0
    demos_taken_state: int = 0
    demos_taken_statfeed: int = 0
    last_demolished: bool = False
    boost_weighted_sum: float = 0.0
    boost_duration: float = 0.0
    speed_weighted_sum: float = 0.0
    speed_duration: float = 0.0
    time_zero_boost: float = 0.0
    time_full_boost: float = 0.0
    time_boosting: float = 0.0
    time_supersonic: float = 0.0
    time_powerslide: float = 0.0
    time_on_ground: float = 0.0
    time_airborne: float = 0.0

    def apply_update(
        self,
        player: PlayerState,
        delta_seconds: float,
        *,
        treat_none_on_ground_as_air: bool = False,
        preserve_scoreboard: bool = False,
    ) -> None:
        self.player_name = player.name or self.player_name
        self.team_num = player.team_num
        self.shortcut = player.shortcut
        if not preserve_scoreboard:
            self.score = player.score
            self.goals = player.goals
            self.assists = player.assists
            self.saves = player.saves
            self.shots = player.shots
            self.touches = player.touches
            self.demos_inflicted_scoreboard = max(self.demos_inflicted_scoreboard, player.demos)

        if delta_seconds <= 0:
            self.last_demolished = bool(player.demolished)
            return

        if player.boost is not None:
            self.boost_weighted_sum += player.boost * delta_seconds
            self.boost_duration += delta_seconds
            if player.boost <= 0:
                self.time_zero_boost += delta_seconds
            if player.boost >= 100:
                self.time_full_boost += delta_seconds

        if player.speed is not None:
            self.speed_weighted_sum += player.speed * delta_seconds
            self.speed_duration += delta_seconds

        if player.boosting:
            self.time_boosting += delta_seconds
        if player.supersonic:
            self.time_supersonic += delta_seconds
        if player.powersliding:
            self.time_powerslide += delta_seconds

        has_car = player.has_car is not False
        if has_car:
            if player.on_ground is True:
                self.time_on_ground += delta_seconds
            elif player.on_ground is False or (
                treat_none_on_ground_as_air and player.on_ground is None
            ):
                self.time_airborne += delta_seconds

        demolished = bool(player.demolished)
        if demolished and not self.last_demolished:
            self.demos_taken_state += 1
        self.last_demolished = demolished

    def add_statfeed_demo_inflicted(self) -> None:
        self.demos_inflicted_statfeed += 1

    def add_statfeed_demo_taken(self) -> None:
        self.demos_taken_statfeed += 1

    def snapshot(
        self,
        opposing_score: int,
        is_user: bool,
        match_duration_seconds: float,
    ) -> PlayerStatsSnapshot:
        dur = max(0.0, float(match_duration_seconds))
        possession_pct = (100.0 * self.possession_seconds / dur) if dur > 0 else 0.0
        stats = {
            "score": float(self.score),
            "goals": float(self.goals),
            "assists": float(self.assists),
            "saves": float(self.saves),
            "shots": float(self.shots),
            "touches": float(self.touches),
            "possession_percentage": possession_pct,
            "shooting_percentage": (self.goals / self.shots * 100) if self.shots else 0.0,
            "goals_conceded": float(opposing_score),
            "demos_inflicted": float(
                max(self.demos_inflicted_scoreboard, self.demos_inflicted_statfeed)
            ),
            "demos_taken": float(max(self.demos_taken_state, self.demos_taken_statfeed)),
            "avg_boost": _weighted_average(self.boost_weighted_sum, self.boost_duration),
            "time_zero_boost": self.time_zero_boost,
            "time_full_boost": self.time_full_boost,
            "avg_speed": _weighted_average(self.speed_weighted_sum, self.speed_duration),
            "time_boosting": self.time_boosting,
            "time_supersonic": self.time_supersonic,
            "time_powerslide": self.time_powerslide,
            "time_on_ground": self.time_on_ground,
            "time_airborne": self.time_airborne,
            "airborne_percentage": _airborne_percentage(self.time_airborne, self.time_on_ground),
        }
        return PlayerStatsSnapshot(
            match_guid=self.match_guid,
            primary_id=self.primary_id,
            player_name=self.player_name,
            team_num=self.team_num,
            is_user=is_user,
            stats=stats,
        )


@dataclass
class MatchAccumulator:
    match_guid: str
    timestamp: datetime
    game_mode: str = "unknown"
    winner_team_num: int | None = None
    duration_seconds: float = 0.0
    last_elapsed: float | None = None
    last_wall_time: datetime | None = None
    players: dict[str, PlayerAccumulator] = field(default_factory=dict)
    teams: dict[int, TeamState] = field(default_factory=dict)
    multiplayer_seen: bool = False
    _possession_holder_id: str | None = None

    def apply_update(
        self,
        event: UpdateStateEvent,
        now: datetime,
        *,
        track_stats: bool,
        treat_none_on_ground_as_air: bool,
    ) -> None:
        self.match_guid = event.match_guid or self.match_guid
        self.game_mode = infer_game_mode(event.players)
        self.teams = {team.team_num: team for team in event.game.teams}
        distinct_ids = {player.primary_id for player in event.players if player.primary_id}
        if len(distinct_ids) >= 2:
            self.multiplayer_seen = True

        delta_seconds = self._calculate_delta(event, now)
        stat_delta = delta_seconds if track_stats else 0.0
        self.duration_seconds += stat_delta
        preserve_scoreboard = not track_stats and self.multiplayer_seen

        prev_touches: dict[str, int] = {}
        for player in event.players:
            if not player.primary_id:
                continue
            acc = self.players.get(player.primary_id)
            prev_touches[player.primary_id] = acc.touches if acc is not None else 0

        if stat_delta > 0 and track_stats and self._possession_holder_id:
            holder = self.players.get(self._possession_holder_id)
            if holder is not None:
                holder.possession_seconds += stat_delta

        for player in event.players:
            if not player.primary_id:
                continue
            accumulator = self.players.get(player.primary_id)
            if accumulator is None:
                accumulator = PlayerAccumulator(
                    match_guid=self.match_guid,
                    primary_id=player.primary_id,
                    player_name=player.name,
                    team_num=player.team_num,
                    shortcut=player.shortcut,
                )
                self.players[player.primary_id] = accumulator
            accumulator.match_guid = self.match_guid
            accumulator.apply_update(
                player,
                stat_delta,
                treat_none_on_ground_as_air=treat_none_on_ground_as_air,
                preserve_scoreboard=preserve_scoreboard,
            )

        for player in event.players:
            if not player.primary_id:
                continue
            if player.touches > prev_touches.get(player.primary_id, 0):
                self._possession_holder_id = player.primary_id

    def player_by_ref(self, ref: PlayerRef | None) -> PlayerAccumulator | None:
        if ref is None:
            return None
        for player in self.players.values():
            if ref.primary_id and player.primary_id == ref.primary_id:
                return player
            if ref.shortcut is not None and player.shortcut == ref.shortcut:
                if ref.team_num is None or player.team_num == ref.team_num:
                    return player
        return None

    def snapshots(self, user_primary_id: str | None) -> tuple[PlayerStatsSnapshot, ...]:
        dur = self.duration_seconds
        return tuple(
            player.snapshot(
                opposing_score=self._opposing_score(player.team_num),
                is_user=player.primary_id == user_primary_id,
                match_duration_seconds=dur,
            )
            for player in self.players.values()
        )

    def team_snapshots(self, user_primary_id: str | None) -> tuple[TeamStatsSnapshot, ...]:
        if not self.teams:
            return ()
        user_team: int | None = None
        if user_primary_id:
            acc = self.players.get(user_primary_id)
            if acc is not None:
                user_team = acc.team_num
        out: list[TeamStatsSnapshot] = []
        for team_num in sorted(self.teams.keys()):
            team = self.teams[team_num]
            mates = [p for p in self.players.values() if p.team_num == team_num]
            goals = sum(p.goals for p in mates)
            assists = sum(p.assists for p in mates)
            saves = sum(p.saves for p in mates)
            shots = sum(p.shots for p in mates)
            touches = sum(p.touches for p in mates)
            possession_seconds = sum(p.possession_seconds for p in mates)
            demos_inflicted = sum(
                max(p.demos_inflicted_scoreboard, p.demos_inflicted_statfeed) for p in mates
            )
            demos_taken = sum(max(p.demos_taken_state, p.demos_taken_statfeed) for p in mates)
            boost_ws = sum(p.boost_weighted_sum for p in mates)
            boost_dur = sum(p.boost_duration for p in mates)
            speed_ws = sum(p.speed_weighted_sum for p in mates)
            speed_dur = sum(p.speed_duration for p in mates)
            time_zero_boost = sum(p.time_zero_boost for p in mates)
            time_full_boost = sum(p.time_full_boost for p in mates)
            time_boosting = sum(p.time_boosting for p in mates)
            time_supersonic = sum(p.time_supersonic for p in mates)
            time_powerslide = sum(p.time_powerslide for p in mates)
            time_on_ground = sum(p.time_on_ground for p in mates)
            time_airborne = sum(p.time_airborne for p in mates)
            opposing_score = max(
                (t.score for t in self.teams.values() if t.team_num != team_num),
                default=0,
            )
            shooting_percentage = (100.0 * goals / shots) if shots else 0.0
            dur = self.duration_seconds
            team_possession_pct = (100.0 * possession_seconds / dur) if dur > 0 else 0.0
            stats: dict[str, float] = {
                "score": float(team.score),
                "goals": float(goals),
                "assists": float(assists),
                "saves": float(saves),
                "shots": float(shots),
                "touches": float(touches),
                "possession_percentage": team_possession_pct,
                "shooting_percentage": shooting_percentage,
                "goals_conceded": float(opposing_score),
                "demos_inflicted": float(demos_inflicted),
                "demos_taken": float(demos_taken),
                "avg_boost": _weighted_average(boost_ws, boost_dur),
                "time_zero_boost": time_zero_boost,
                "time_full_boost": time_full_boost,
                "avg_speed": _weighted_average(speed_ws, speed_dur),
                "time_boosting": time_boosting,
                "time_supersonic": time_supersonic,
                "time_powerslide": time_powerslide,
                "time_on_ground": time_on_ground,
                "time_airborne": time_airborne,
                "airborne_percentage": _airborne_percentage(time_airborne, time_on_ground),
            }
            out.append(
                TeamStatsSnapshot(
                    team_num=team_num,
                    team_name=team.name,
                    is_user_team=user_team is not None and team_num == user_team,
                    stats=stats,
                )
            )
        return tuple(out)

    def _opposing_score(self, team_num: int) -> int:
        opposing_scores = [team.score for team in self.teams.values() if team.team_num != team_num]
        return max(opposing_scores, default=0)

    def _calculate_delta(self, event: UpdateStateEvent, now: datetime) -> float:
        if event.game.elapsed is not None:
            if self.last_elapsed is None:
                self.last_elapsed = event.game.elapsed
                self.last_wall_time = now
                return 0.0
            delta = max(0.0, event.game.elapsed - self.last_elapsed)
            self.last_elapsed = event.game.elapsed
            self.last_wall_time = now
            return min(delta, 5.0)

        if self.last_wall_time is None:
            self.last_wall_time = now
            return 0.0
        delta = max(0.0, (now - self.last_wall_time).total_seconds())
        self.last_wall_time = now
        return min(delta, 5.0)


class DemolyticsAggregator:
    def __init__(self, user_primary_id: str | None = None, repository: Any = None) -> None:
        self.user_primary_id = user_primary_id
        self.repository = repository
        self.active_session: SessionSnapshot | None = None
        self.current_match: MatchAccumulator | None = None
        self._status = "Waiting for Rocket League"
        self._paused_until_kickoff = False
        self._last_team_score_sum: int | None = None
        self._block_derived_stats = True
        self._frozen_user_stats_between_matches: dict[str, float] | None = None
        self._frozen_user_team_stats: dict[str, float] | None = None
        self._session_outcome_recorded_for: set[str] = set()
        self._goal_insight: str | None = None
        self._prev_team_scores: dict[int, int] = {}
        self._prev_player_goals: dict[str, int] = {}
        self._match_distinct_id_peak: int = 0
        self._current_match_start_wall: datetime | None = None

    def reset_tracking_state(self) -> None:
        self.user_primary_id = None
        self.active_session = None
        self.current_match = None
        self._paused_until_kickoff = False
        self._last_team_score_sum = None
        self._block_derived_stats = True
        self._frozen_user_stats_between_matches = None
        self._frozen_user_team_stats = None
        self._session_outcome_recorded_for = set()
        self._goal_insight = None
        self._prev_team_scores = {}
        self._prev_player_goals = {}
        self._match_distinct_id_peak = 0
        self._current_match_start_wall = None
        self._status = "Waiting for Rocket League"

    def reset_session_and_frozen_stats_after_db_performance_clear(self) -> None:
        """Align in-memory session counters and frozen post-match stats with a DB stats reset."""
        if self.active_session is not None:
            s = self.active_session
            self.active_session = SessionSnapshot(
                session_id=s.session_id,
                game_mode=s.game_mode,
                start_time=s.start_time,
                wins=0,
                losses=0,
                win_streak=0,
            )
        self._frozen_user_stats_between_matches = None
        self._frozen_user_team_stats = None

    def handle_event(
        self,
        event: StatsEvent,
        now: datetime | None = None,
    ) -> AggregationResult:
        now = now or datetime.now(UTC)
        started_session: SessionSnapshot | None = None
        completed_match: CompletedMatch | None = None

        if isinstance(event, UpdateStateEvent):
            started_session = self._handle_update_state(event, now)
        elif isinstance(event, MatchEndedEvent):
            self._handle_match_ended(event)
        elif isinstance(event, MatchLifecycleEvent):
            completed_match = self._handle_lifecycle(event, now)
        elif isinstance(event, StatfeedEvent):
            self._handle_statfeed(event)

        return AggregationResult(
            snapshot=self.snapshot(),
            started_session=started_session,
            completed_match=completed_match,
        )

    def snapshot(self) -> DashboardSnapshot:
        live_players: tuple[PlayerStatsSnapshot, ...] = ()
        live_teams: tuple[TeamStatsSnapshot, ...] = ()
        live_user_stats: dict[str, float] = {}
        user_team_stats: dict[str, float] = {}
        current_match_guid: str | None = None
        current_game_mode = self.active_session.game_mode if self.active_session else "unknown"

        if self.current_match:
            current_match_guid = self.current_match.match_guid
            current_game_mode = self.current_match.game_mode
            live_players = self.current_match.snapshots(self.user_primary_id)
            live_teams = self.current_match.team_snapshots(self.user_primary_id)
            user_snapshot = next((player for player in live_players if player.is_user), None)
            prefer_frozen_live = (
                self._frozen_user_stats_between_matches is not None
                and self._block_derived_stats
                and (not self.current_match.multiplayer_seen or len(live_players) < 2)
            )
            if user_snapshot is not None and not prefer_frozen_live:
                live_user_stats = user_snapshot.stats
            elif self._frozen_user_stats_between_matches is not None and (
                user_snapshot is None
                or prefer_frozen_live
                or (not self.current_match.multiplayer_seen or len(live_players) < 2)
            ):
                live_user_stats = dict(self._frozen_user_stats_between_matches)
            ut = next((t for t in live_teams if t.is_user_team), None)
            prefer_frozen_team = (
                self._frozen_user_team_stats is not None
                and self._block_derived_stats
                and (not self.current_match.multiplayer_seen or len(live_players) < 2)
            )
            if ut is not None and not prefer_frozen_team:
                user_team_stats = dict(ut.stats)
            elif self._frozen_user_team_stats is not None and (
                ut is None
                or prefer_frozen_team
                or (not self.current_match.multiplayer_seen or len(live_players) < 2)
            ):
                user_team_stats = dict(self._frozen_user_team_stats)
        elif self._frozen_user_stats_between_matches is not None:
            live_user_stats = dict(self._frozen_user_stats_between_matches)
        if (
            self.current_match is None
            and not user_team_stats
            and self._frozen_user_team_stats is not None
        ):
            user_team_stats = dict(self._frozen_user_team_stats)

        return DashboardSnapshot(
            session=self.active_session,
            current_match_guid=current_match_guid,
            current_game_mode=current_game_mode,
            live_user_stats=live_user_stats,
            user_team_stats=user_team_stats,
            live_players=live_players,
            live_teams=live_teams,
            goal_insight=self._goal_insight,
            user_primary_id=self.user_primary_id,
            status=self._status,
            win_streak=self.active_session.win_streak if self.active_session else 0,
        )

    def end_active_session(self) -> None:
        self.active_session = None
        self._frozen_user_stats_between_matches = None
        self._frozen_user_team_stats = None
        self._goal_insight = None
        self._status = "Session ended"

    def _handle_update_state(
        self,
        event: UpdateStateEvent,
        now: datetime,
    ) -> SessionSnapshot | None:
        match_guid = event.match_guid or self.current_match_guid_or_new()
        if self.current_match is None or self.current_match.match_guid != match_guid:
            if self.current_match is not None:
                self._ensure_winner_and_record_session_outcome(self.current_match)
                if (
                    self.current_match.multiplayer_seen
                    and self.user_primary_id
                    and is_freeplay(event.players)
                ):
                    for player in self.current_match.snapshots(self.user_primary_id):
                        if player.primary_id == self.user_primary_id:
                            self._frozen_user_stats_between_matches = dict(player.stats)
                            break
                    ut = next(
                        (
                            t
                            for t in self.current_match.team_snapshots(self.user_primary_id)
                            if t.is_user_team
                        ),
                        None,
                    )
                    if ut is not None:
                        self._frozen_user_team_stats = dict(ut.stats)
            self.current_match = MatchAccumulator(match_guid=match_guid, timestamp=now)
            self._paused_until_kickoff = False
            self._last_team_score_sum = None
            self._prev_team_scores = {}
            self._prev_player_goals = {}
            self._match_distinct_id_peak = 0
            self._current_match_start_wall = now
            self._goal_insight = None

        new_team_scores = {t.team_num: t.score for t in event.game.teams}
        scoring_teams = [
            tn for tn, sc in new_team_scores.items() if sc > self._prev_team_scores.get(tn, 0)
        ]
        goal_team = scoring_teams[0] if len(scoring_teams) == 1 else None
        self._sync_kickoff_pause_latch(event.game)
        freeplay = is_freeplay(event.players)
        if (
            freeplay
            and self.current_match is not None
            and self.current_match.multiplayer_seen
        ):
            self._ensure_winner_and_record_session_outcome(self.current_match)
        track_stats = self._should_accumulate_match_stats(event.game, freeplay)
        self._block_derived_stats = not track_stats
        distinct_ids = len({p.primary_id for p in event.players if p.primary_id})
        self._match_distinct_id_peak = max(self._match_distinct_id_peak, distinct_ids)
        inferred_mode = infer_game_mode_from_count(self._match_distinct_id_peak)
        t_in = max(
            (now - self._current_match_start_wall).total_seconds()
            if self._current_match_start_wall
            else 0.0,
            float(event.game.elapsed or 0.0),
        )
        inferred_for_session = _inferred_mode_for_session(
            inferred_mode, self._match_distinct_id_peak, t_in, self.active_session
        )
        treat_none_air = inferred_mode in SUPPORTED_MODES and not freeplay

        self.current_match.apply_update(
            event,
            now,
            track_stats=track_stats,
            treat_none_on_ground_as_air=treat_none_air,
        )
        if inferred_for_session == "unknown" and self.active_session is not None:
            self.current_match.game_mode = self.active_session.game_mode
        else:
            self.current_match.game_mode = inferred_mode
        self._resolve_user_from_target(event)

        if (
            goal_team is not None
            and inferred_mode in SUPPORTED_MODES
            and not freeplay
            and self.current_match.multiplayer_seen
        ):
            live_players = self.current_match.snapshots(self.user_primary_id)
            stat_seconds = self.current_match.duration_seconds
            if event.game.elapsed is not None:
                stat_seconds = max(stat_seconds, float(event.game.elapsed))
            insight_salt = zlib.adler32(
                f"{self.current_match.match_guid}|{sum(new_team_scores.values())}".encode()
            )
            historical = None
            if (
                self.repository is not None
                and self.user_primary_id
                and inferred_mode in SUPPORTED_MODES
            ):
                try:
                    historical = self.repository.fetch_rolling_goal_insight_baselines(
                        self.user_primary_id,
                        inferred_mode,
                        exclude_match_guid=self.current_match.match_guid,
                    )
                except Exception:
                    LOGGER.exception("Failed to load goal insight historical baselines")
            insight = compute_goal_insight(
                live_players,
                stat_seconds,
                insight_salt=insight_salt,
                historical=historical,
            )
            if insight is not None:
                self._goal_insight = insight.message
                append_goal_insight_log(
                    {
                        "schema_version": 1,
                        "ts": now.isoformat(),
                        "match_guid": self.current_match.match_guid,
                        "session_id": self.active_session.session_id if self.active_session else None,
                        "game_mode": self.current_match.game_mode,
                        "total_goals_in_match": int(sum(new_team_scores.values())),
                        "match_duration_seconds": stat_seconds,
                        "message": insight.message,
                        "stat_key": insight.stat_key,
                        "kind": insight.kind,
                        "peer_group": insight.peer_group,
                        "user_value": insight.user_value,
                        "peer_median": insight.peer_median,
                        "ratio": insight.ratio,
                        "outlier_direction": insight.outlier_direction,
                    }
                )
                LOGGER.info(
                    "Goal insight logged: kind=%s stat_key=%s peer_group=%s",
                    insight.kind,
                    insight.stat_key,
                    insight.peer_group,
                )

        self._prev_team_scores = dict(new_team_scores)
        for player in event.players:
            if player.primary_id:
                self._prev_player_goals[player.primary_id] = player.goals

        if inferred_for_session in SUPPORTED_MODES:
            return self._ensure_session_for_mode(inferred_for_session, now)

        self._status = "Waiting for enough player data to infer mode"
        return None

    def _should_accumulate_match_stats(self, game: GameState, freeplay: bool) -> bool:
        if freeplay:
            return False
        if game.replay or game.has_winner:
            return False
        if self._paused_until_kickoff:
            return False
        return True

    def _sync_kickoff_pause_latch(self, game: GameState) -> None:
        """Hold stats from goal-scoring through replay and kickoff countdown until RoundStarted."""
        if game.replay:
            self._paused_until_kickoff = True
        total_score = sum(team.score for team in game.teams)
        if self._last_team_score_sum is not None and total_score > self._last_team_score_sum:
            self._paused_until_kickoff = True
        self._last_team_score_sum = total_score

    def _handle_match_ended(self, event: MatchEndedEvent) -> None:
        if self.current_match is None:
            return
        if event.match_guid and self.current_match.match_guid != event.match_guid:
            return
        if event.winner_team_num is not None:
            self.current_match.winner_team_num = event.winner_team_num
        self._record_session_outcome_for_match(self.current_match)

    def _infer_winner_team_from_scores(self, match: MatchAccumulator) -> int | None:
        if len(match.teams) < 2:
            return None
        top = max(team.score for team in match.teams.values())
        leaders = [t for t in match.teams.values() if t.score == top]
        if len(leaders) != 1:
            return None
        return leaders[0].team_num

    def _ensure_winner_and_record_session_outcome(self, match: MatchAccumulator) -> None:
        if match.winner_team_num is None:
            inferred = self._infer_winner_team_from_scores(match)
            if inferred is not None:
                match.winner_team_num = inferred
        self._record_session_outcome_for_match(match)

    def _record_session_outcome_for_match(self, match: MatchAccumulator) -> None:
        if not match.multiplayer_seen or self.active_session is None:
            return
        if match.match_guid in self._session_outcome_recorded_for:
            return
        winner = match.winner_team_num
        if winner is None:
            winner = self._infer_winner_team_from_scores(match)
            if winner is None:
                return
            match.winner_team_num = winner
        user_team = self._user_team_num_for_match(match)
        if user_team is None:
            return

        self._session_outcome_recorded_for.add(match.match_guid)
        wins = self.active_session.wins + int(winner == user_team)
        losses = self.active_session.losses + int(winner != user_team)
        win_streak = self.active_session.win_streak + 1 if winner == user_team else 0
        self.active_session = SessionSnapshot(
            session_id=self.active_session.session_id,
            game_mode=self.active_session.game_mode,
            start_time=self.active_session.start_time,
            wins=wins,
            losses=losses,
            win_streak=win_streak,
        )

    def _user_team_num_for_match(self, match: MatchAccumulator) -> int | None:
        if self.user_primary_id is None:
            return None
        player = match.players.get(self.user_primary_id)
        return player.team_num if player else None

    def _handle_lifecycle(
        self,
        event: MatchLifecycleEvent,
        now: datetime,
    ) -> CompletedMatch | None:
        if event.event_name == "RoundStarted":
            self._paused_until_kickoff = False

        if event.event_name in {"MatchCreated", "MatchInitialized"}:
            match_guid = event.match_guid or self.current_match_guid_or_new()
            if self.current_match is not None and self.current_match.match_guid != match_guid:
                self._ensure_winner_and_record_session_outcome(self.current_match)
            if self.current_match is None or self.current_match.match_guid != match_guid:
                self.current_match = MatchAccumulator(match_guid=match_guid, timestamp=now)
                self._match_distinct_id_peak = 0
                self._current_match_start_wall = now
            self._paused_until_kickoff = False
            self._last_team_score_sum = None
            self._prev_team_scores = {}
            self._prev_player_goals = {}
            self._goal_insight = None
            self._status = f"{event.event_name} received"
            return None

        if event.event_name == "MatchDestroyed" and self.current_match is not None:
            self._ensure_winner_and_record_session_outcome(self.current_match)
            frozen_team_stats: dict[str, float] | None = None
            if self.user_primary_id:
                ut = next(
                    (t for t in self.current_match.team_snapshots(self.user_primary_id) if t.is_user_team),
                    None,
                )
                if ut is not None:
                    frozen_team_stats = dict(ut.stats)
            completed = self._complete_current_match(now)
            if completed is not None and self.user_primary_id:
                for player in completed.players:
                    if player.primary_id == self.user_primary_id:
                        self._frozen_user_stats_between_matches = dict(player.stats)
                        break
                if frozen_team_stats is not None:
                    self._frozen_user_team_stats = frozen_team_stats
            self.current_match = None
            self._paused_until_kickoff = False
            self._last_team_score_sum = None
            self._prev_team_scores = {}
            self._prev_player_goals = {}
            self._goal_insight = None
            self._match_distinct_id_peak = 0
            self._current_match_start_wall = None
            self._status = "Match saved" if completed is not None else "Match not saved (training/freeplay)"
            return completed

        return None

    def _handle_statfeed(self, event: StatfeedEvent) -> None:
        if self.current_match is None or event.stat_type.lower() != "demolition":
            return
        if self._block_derived_stats:
            return
        if len(self.current_match.players) <= 1:
            return
        inflicter = self.current_match.player_by_ref(event.main_target)
        victim = self.current_match.player_by_ref(event.secondary_target)
        if inflicter is not None:
            inflicter.add_statfeed_demo_inflicted()
        if victim is not None:
            victim.add_statfeed_demo_taken()

    def _ensure_session_for_mode(
        self,
        game_mode: str,
        now: datetime,
    ) -> SessionSnapshot | None:
        if self.active_session and self.active_session.game_mode == game_mode:
            self._status = f"Tracking {game_mode}"
            return None

        self._frozen_user_stats_between_matches = None
        self._frozen_user_team_stats = None
        self.active_session = SessionSnapshot(
            session_id=str(uuid.uuid4()),
            game_mode=game_mode,
            start_time=now,
        )
        self._status = f"Started {game_mode} session"
        return self.active_session

    def _complete_current_match(self, now: datetime) -> CompletedMatch | None:
        if self.current_match is None or self.active_session is None:
            return None
        if not self.current_match.multiplayer_seen:
            return None
        user_result = self._user_result()
        return CompletedMatch(
            match_guid=self.current_match.match_guid,
            session_id=self.active_session.session_id,
            timestamp=now,
            game_mode=self.current_match.game_mode,
            user_result=user_result,
            duration_seconds=self.current_match.duration_seconds,
            players=self.current_match.snapshots(self.user_primary_id),
        )

    def _user_result(self) -> str | None:
        if self.current_match is None or self.current_match.winner_team_num is None:
            return None
        user_team = self._user_team_num()
        if user_team is None:
            return None
        return "Win" if self.current_match.winner_team_num == user_team else "Loss"

    def _user_team_num(self) -> int | None:
        if self.current_match is None or self.user_primary_id is None:
            return None
        player = self.current_match.players.get(self.user_primary_id)
        return player.team_num if player else None

    def _resolve_user_from_target(self, event: UpdateStateEvent) -> None:
        if self.user_primary_id:
            return
        target = event.game.target
        if target is not None:
            for player in event.players:
                if target.shortcut is not None and player.shortcut == target.shortcut:
                    if target.team_num is None or player.team_num == target.team_num:
                        self.user_primary_id = player.primary_id
                        return
                if target.primary_id and player.primary_id == target.primary_id:
                    self.user_primary_id = player.primary_id
                    return
        self._resolve_user_heuristic(event.players)

    def _resolve_user_heuristic(self, players: tuple[PlayerState, ...]) -> None:
        """When Game.Target is missing, infer the local client as the only player with full car telemetry."""
        distinct = {p.primary_id for p in players if p.primary_id}
        if len(distinct) < 2:
            return
        rich = [
            p
            for p in players
            if p.primary_id and p.boost is not None and p.speed is not None
        ]
        if len(rich) == 1:
            self.user_primary_id = rich[0].primary_id

    def current_match_guid_or_new(self) -> str:
        if self.current_match is not None:
            return self.current_match.match_guid
        return f"local-{uuid.uuid4()}"


def infer_game_mode_from_count(player_count: int) -> str:
    return {2: "1v1", 4: "2v2", 6: "3v3"}.get(player_count, "unknown")


def _inferred_mode_for_session(
    mode_from_peak: str,
    peak: int,
    time_in_match: float,
    active_session: SessionSnapshot | None,
) -> str:
    """2v2 load often shows 1–2 PrimaryIds first; don't flip the session to 1v1 until the lobby stabilizes."""
    if (
        mode_from_peak == "1v1"
        and peak < 4
        and time_in_match < MODE_SESSION_LOADING_SECONDS
        and active_session is not None
        and active_session.game_mode == "2v2"
    ):
        return "unknown"
    return mode_from_peak


def infer_game_mode(players: tuple[PlayerState, ...]) -> str:
    return infer_game_mode_from_count(
        len({p.primary_id for p in players if p.primary_id}),
    )


def is_freeplay(players: tuple[PlayerState, ...]) -> bool:
    """True when only one distinct PrimaryId is present (training / freeplay lobby)."""
    distinct = {player.primary_id for player in players if player.primary_id}
    return len(distinct) <= 1


def _airborne_percentage(time_airborne: float, time_on_ground: float) -> float:
    total = time_airborne + time_on_ground
    if total <= 0:
        return 0.0
    return 100.0 * time_airborne / total


def _weighted_average(weighted_sum: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    return weighted_sum / duration
