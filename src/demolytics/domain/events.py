from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlayerRef:
    name: str = ""
    shortcut: int | None = None
    team_num: int | None = None
    primary_id: str | None = None


@dataclass(frozen=True)
class TeamState:
    name: str
    team_num: int
    score: int


@dataclass(frozen=True)
class PlayerState:
    name: str
    primary_id: str
    shortcut: int | None
    team_num: int
    score: int = 0
    goals: int = 0
    shots: int = 0
    assists: int = 0
    saves: int = 0
    touches: int = 0
    car_touches: int = 0
    demos: int = 0
    has_car: bool | None = None
    speed: float | None = None
    boost: int | None = None
    boosting: bool | None = None
    on_ground: bool | None = None
    on_wall: bool | None = None
    powersliding: bool | None = None
    demolished: bool | None = None
    supersonic: bool | None = None
    attacker: PlayerRef | None = None


@dataclass(frozen=True)
class GameState:
    teams: tuple[TeamState, ...]
    time_seconds: int | None = None
    overtime: bool = False
    replay: bool = False
    has_winner: bool = False
    winner: str = ""
    arena: str = ""
    target: PlayerRef | None = None
    frame: int | None = None
    elapsed: float | None = None


@dataclass(frozen=True)
class UpdateStateEvent:
    match_guid: str
    players: tuple[PlayerState, ...]
    game: GameState
    event_name: str = "UpdateState"


@dataclass(frozen=True)
class MatchLifecycleEvent:
    event_name: str
    match_guid: str


@dataclass(frozen=True)
class MatchEndedEvent:
    match_guid: str
    winner_team_num: int | None
    event_name: str = "MatchEnded"


@dataclass(frozen=True)
class StatfeedEvent:
    match_guid: str
    event_name: str
    stat_name: str
    stat_type: str
    main_target: PlayerRef
    secondary_target: PlayerRef | None = None


@dataclass(frozen=True)
class GenericEvent:
    event_name: str
    data: dict[str, Any]
    match_guid: str = ""


StatsEvent = (
    UpdateStateEvent
    | MatchLifecycleEvent
    | MatchEndedEvent
    | StatfeedEvent
    | GenericEvent
)

LIFECYCLE_EVENTS = {
    "MatchCreated",
    "MatchInitialized",
    "MatchDestroyed",
    "MatchPaused",
    "MatchUnpaused",
    "PodiumStart",
    "ReplayCreated",
    "RoundStarted",
    "CountdownBegin",
}


def _event_payload_as_dict(raw_data: Any) -> dict[str, Any]:
    """Rocket League sends `Data` as either an object or a JSON-encoded string."""
    if raw_data is None:
        return {}
    if isinstance(raw_data, dict):
        return raw_data
    if isinstance(raw_data, str):
        try:
            inner = json.loads(raw_data)
        except json.JSONDecodeError:
            return {}
        return inner if isinstance(inner, dict) else {}
    return {}


def parse_message(raw: str | bytes | dict[str, Any]) -> StatsEvent:
    envelope = _load_envelope(raw)
    event_name = str(envelope.get("Event", ""))
    data = _event_payload_as_dict(envelope.get("Data"))

    if event_name == "UpdateState":
        return _parse_update_state(data)
    if event_name == "MatchEnded":
        return MatchEndedEvent(
            match_guid=str(data.get("MatchGuid", "")),
            winner_team_num=_optional_int(data.get("WinnerTeamNum")),
        )
    if event_name == "StatfeedEvent":
        return StatfeedEvent(
            match_guid=str(data.get("MatchGuid", "")),
            event_name=event_name,
            stat_name=str(data.get("EventName", "")),
            stat_type=str(data.get("Type", "")),
            main_target=_parse_player_ref(data.get("MainTarget")),
            secondary_target=_parse_optional_player_ref(data.get("SecondaryTarget")),
        )
    if event_name in LIFECYCLE_EVENTS:
        return MatchLifecycleEvent(
            event_name=event_name,
            match_guid=str(data.get("MatchGuid", "")),
        )
    return GenericEvent(
        event_name=event_name,
        data=data,
        match_guid=str(data.get("MatchGuid", "")),
    )


def _load_envelope(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Stats API message must be a JSON object.")
    return loaded


def _parse_update_state(data: dict[str, Any]) -> UpdateStateEvent:
    players = tuple(_parse_player_state(player) for player in data.get("Players", []))
    return UpdateStateEvent(
        match_guid=str(data.get("MatchGuid", "")),
        players=players,
        game=_parse_game_state(data.get("Game") or {}),
    )


def _parse_player_state(data: dict[str, Any]) -> PlayerState:
    on_ground = _optional_bool(data.get("bOnGround"))
    if on_ground is None:
        on_ground = _optional_bool(data.get("bHasWheelContact"))
    return PlayerState(
        name=str(data.get("Name", "")),
        primary_id=str(data.get("PrimaryId", "")),
        shortcut=_optional_int(data.get("Shortcut")),
        team_num=_int(data.get("TeamNum")),
        score=_int(data.get("Score")),
        goals=_int(data.get("Goals")),
        shots=_int(data.get("Shots")),
        assists=_int(data.get("Assists")),
        saves=_int(data.get("Saves")),
        touches=_int(data.get("Touches")),
        car_touches=_int(data.get("CarTouches")),
        demos=_int(data.get("Demos")),
        has_car=_optional_bool(data.get("bHasCar")),
        speed=_optional_float(data.get("Speed")),
        boost=_optional_int(data.get("Boost")),
        boosting=_optional_bool(data.get("bBoosting")),
        on_ground=on_ground,
        on_wall=_optional_bool(data.get("bOnWall")),
        powersliding=_optional_bool(data.get("bPowersliding")),
        demolished=_optional_bool(data.get("bDemolished")),
        supersonic=_optional_bool(data.get("bSupersonic")),
        attacker=_parse_optional_player_ref(data.get("Attacker")),
    )


def _parse_game_state(data: dict[str, Any]) -> GameState:
    teams = tuple(_parse_team_state(team) for team in data.get("Teams", []))
    return GameState(
        teams=teams,
        time_seconds=_optional_int(data.get("TimeSeconds")),
        overtime=bool(data.get("bOvertime", False)),
        replay=bool(data.get("bReplay", False)),
        has_winner=bool(data.get("bHasWinner", False)),
        winner=str(data.get("Winner", "")),
        arena=str(data.get("Arena", "")),
        target=_parse_optional_player_ref(data.get("Target")),
        frame=_optional_int(data.get("Frame")),
        elapsed=_optional_float(data.get("Elapsed")),
    )


def _parse_team_state(data: dict[str, Any]) -> TeamState:
    return TeamState(
        name=str(data.get("Name", "")),
        team_num=_int(data.get("TeamNum")),
        score=_int(data.get("Score")),
    )


def _parse_optional_player_ref(data: Any) -> PlayerRef | None:
    if not isinstance(data, dict):
        return None
    ref = _parse_player_ref(data)
    if not ref.name and ref.shortcut in (None, 0) and ref.team_num in (None, 0):
        return None
    return ref


def _parse_player_ref(data: Any) -> PlayerRef:
    if not isinstance(data, dict):
        return PlayerRef()
    return PlayerRef(
        name=str(data.get("Name", "")),
        shortcut=_optional_int(data.get("Shortcut")),
        team_num=_optional_int(data.get("TeamNum")),
        primary_id=data.get("PrimaryId"),
    )


def _int(value: Any, default: int = 0) -> int:
    parsed = _optional_int(value)
    return default if parsed is None else parsed


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
