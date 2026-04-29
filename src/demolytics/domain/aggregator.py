from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

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

SUPPORTED_MODES = {"1v1", "2v2", "3v3"}


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
    live_players: tuple[PlayerStatsSnapshot, ...]
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
    ) -> None:
        self.player_name = player.name or self.player_name
        self.team_num = player.team_num
        self.shortcut = player.shortcut
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
    ) -> PlayerStatsSnapshot:
        stats = {
            "score": float(self.score),
            "goals": float(self.goals),
            "assists": float(self.assists),
            "saves": float(self.saves),
            "shots": float(self.shots),
            "touches": float(self.touches),
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
            )

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
        return tuple(
            player.snapshot(
                opposing_score=self._opposing_score(player.team_num),
                is_user=player.primary_id == user_primary_id,
            )
            for player in self.players.values()
        )

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
    def __init__(self, user_primary_id: str | None = None) -> None:
        self.user_primary_id = user_primary_id
        self.active_session: SessionSnapshot | None = None
        self.current_match: MatchAccumulator | None = None
        self._status = "Waiting for Rocket League"
        self._stats_tracking_excluded = False

    def reset_tracking_state(self) -> None:
        self.user_primary_id = None
        self.active_session = None
        self.current_match = None
        self._stats_tracking_excluded = False
        self._status = "Waiting for Rocket League"

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
        live_user_stats: dict[str, float] = {}
        current_match_guid: str | None = None
        current_game_mode = self.active_session.game_mode if self.active_session else "unknown"

        if self.current_match:
            current_match_guid = self.current_match.match_guid
            current_game_mode = self.current_match.game_mode
            live_players = self.current_match.snapshots(self.user_primary_id)
            user_snapshot = next((player for player in live_players if player.is_user), None)
            if user_snapshot is not None:
                live_user_stats = user_snapshot.stats

        return DashboardSnapshot(
            session=self.active_session,
            current_match_guid=current_match_guid,
            current_game_mode=current_game_mode,
            live_user_stats=live_user_stats,
            live_players=live_players,
            user_primary_id=self.user_primary_id,
            status=self._status,
            win_streak=self.active_session.win_streak if self.active_session else 0,
        )

    def end_active_session(self) -> None:
        self.active_session = None
        self._status = "Session ended"

    def _handle_update_state(
        self,
        event: UpdateStateEvent,
        now: datetime,
    ) -> SessionSnapshot | None:
        match_guid = event.match_guid or self.current_match_guid_or_new()
        if self.current_match is None or self.current_match.match_guid != match_guid:
            self.current_match = MatchAccumulator(match_guid=match_guid, timestamp=now)
            self._stats_tracking_excluded = False

        self._sync_exclusion_from_game(event.game)
        freeplay = is_freeplay(event.players)
        track_stats = not freeplay and not self._stats_tracking_excluded
        inferred_mode = infer_game_mode(event.players)
        treat_none_air = inferred_mode in SUPPORTED_MODES and not freeplay

        self.current_match.apply_update(
            event,
            now,
            track_stats=track_stats,
            treat_none_on_ground_as_air=treat_none_air,
        )
        self._resolve_user_from_target(event)

        if inferred_mode in SUPPORTED_MODES:
            return self._ensure_session_for_mode(inferred_mode, now)

        self._status = "Waiting for enough player data to infer mode"
        return None

    def _sync_exclusion_from_game(self, game: GameState) -> None:
        """Pause stat accumulation during replays and after a goal until play resumes."""
        if game.replay or game.has_winner:
            self._stats_tracking_excluded = True
        else:
            self._stats_tracking_excluded = False

    def _handle_match_ended(self, event: MatchEndedEvent) -> None:
        if self.current_match is None:
            return
        self.current_match.winner_team_num = event.winner_team_num
        user_team = self._user_team_num()
        if user_team is None or event.winner_team_num is None or self.active_session is None:
            return

        wins = self.active_session.wins + int(event.winner_team_num == user_team)
        losses = self.active_session.losses + int(event.winner_team_num != user_team)
        win_streak = (
            self.active_session.win_streak + 1
            if event.winner_team_num == user_team
            else 0
        )
        self.active_session = SessionSnapshot(
            session_id=self.active_session.session_id,
            game_mode=self.active_session.game_mode,
            start_time=self.active_session.start_time,
            wins=wins,
            losses=losses,
            win_streak=win_streak,
        )

    def _handle_lifecycle(
        self,
        event: MatchLifecycleEvent,
        now: datetime,
    ) -> CompletedMatch | None:
        if event.event_name in {"CountdownBegin", "RoundStarted"}:
            self._stats_tracking_excluded = False

        if event.event_name in {"MatchCreated", "MatchInitialized"}:
            match_guid = event.match_guid or self.current_match_guid_or_new()
            if self.current_match is None or self.current_match.match_guid != match_guid:
                self.current_match = MatchAccumulator(match_guid=match_guid, timestamp=now)
                self._stats_tracking_excluded = False
            self._status = f"{event.event_name} received"
            return None

        if event.event_name == "MatchDestroyed" and self.current_match is not None:
            completed = self._complete_current_match(now)
            self.current_match = None
            self._status = "Match saved" if completed is not None else "Match not saved (training/freeplay)"
            return completed

        return None

    def _handle_statfeed(self, event: StatfeedEvent) -> None:
        if self.current_match is None or event.stat_type.lower() != "demolition":
            return
        if self._stats_tracking_excluded:
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
        if self.user_primary_id or event.game.target is None:
            return
        target = event.game.target
        for player in event.players:
            if target.shortcut is not None and player.shortcut == target.shortcut:
                if target.team_num is None or player.team_num == target.team_num:
                    self.user_primary_id = player.primary_id
                    return

    def current_match_guid_or_new(self) -> str:
        if self.current_match is not None:
            return self.current_match.match_guid
        return f"local-{uuid.uuid4()}"


def infer_game_mode(players: tuple[PlayerState, ...]) -> str:
    player_count = len({player.primary_id for player in players if player.primary_id})
    return {
        2: "1v1",
        4: "2v2",
        6: "3v3",
    }.get(player_count, "unknown")


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
