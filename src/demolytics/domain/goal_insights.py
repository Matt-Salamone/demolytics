from __future__ import annotations

from typing import Callable, Protocol

from demolytics.domain.stats import STAT_LABELS

# Floor for "enough" match time so team/player stats are comparable (in-game clock or accumulated).
# Kept low enough that early goals still get insights when the outlier logic finds a signal.
MIN_LOBBY_SECONDS = 4.0
OUTLIER_RATIO = 1.5


class _PlayerStatsLike(Protocol):
    primary_id: str
    player_name: str
    team_num: int
    stats: dict[str, float]


class _TeamStatsLike(Protocol):
    team_num: int
    team_name: str
    stats: dict[str, float]


def compute_goal_insight(
    scoring_team_num: int,
    players: tuple[_PlayerStatsLike, ...],
    teams: tuple[_TeamStatsLike, ...],
    match_duration_seconds: float,
    scorer_primary_id: str | None,
    *,
    user_team_num: int | None = None,
) -> str | None:
    distinct = {p.primary_id for p in players if p.primary_id}
    if len(distinct) < 2:
        return None
    if match_duration_seconds < MIN_LOBBY_SECONDS:
        return None

    best_ratio = 0.0
    best_message: str | None = None

    def consider(ratio: float, message: str) -> None:
        nonlocal best_ratio, best_message
        if ratio > best_ratio and ratio >= OUTLIER_RATIO:
            best_ratio = ratio
            best_message = message

    _player_outliers(players, consider, user_team_num=user_team_num)
    _team_outliers(teams, consider)

    if best_message is None:
        return None

    prefix = ""
    if scorer_primary_id:
        scorer = next((p for p in players if p.primary_id == scorer_primary_id), None)
        if scorer is not None:
            team_label = next(
                (t.team_name for t in teams if t.team_num == scoring_team_num),
                f"Team {scoring_team_num}",
            )
            name = (scorer.player_name or "Player").strip() or "Player"
            prefix = f"Goal — {name} ({team_label}). "

    return prefix + best_message


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _player_outliers(
    players: tuple[_PlayerStatsLike, ...],
    consider: Callable[[float, str], None],
    *,
    user_team_num: int | None,
) -> None:
    """Only compare teammates — opponent stats are often missing or stale in live RL."""
    if user_team_num is None:
        return
    lobby = [p for p in players if p.primary_id and p.team_num == user_team_num]
    if len(lobby) < 2:
        return

    for stat_key, higher_is_notable in (
        ("time_zero_boost", True),
        ("demos_inflicted", True),
        ("demos_taken", True),
        ("airborne_percentage", True),
    ):
        values = [float(p.stats.get(stat_key, 0.0)) for p in lobby]
        med = _median(values)
        if med <= 1e-6:
            continue
        label = STAT_LABELS.get(stat_key, stat_key)
        for p, v in zip(lobby, values, strict=True):
            if higher_is_notable:
                ratio = v / med
                if ratio >= OUTLIER_RATIO:
                    name = (p.player_name or p.primary_id).strip() or p.primary_id
                    consider(
                        ratio,
                        f"{name} leads your team in {label} ({_fmt(stat_key, v)} vs team median {_fmt(stat_key, med)}).",
                    )

    stat_key = "avg_boost"
    values = [float(p.stats.get(stat_key, 0.0)) for p in lobby]
    med = _median(values)
    if med > 1e-6:
        label = STAT_LABELS.get(stat_key, stat_key)
        for p, v in zip(lobby, values, strict=True):
            if v < med and med / max(v, 1e-6) >= OUTLIER_RATIO:
                ratio = med / max(v, 1e-6)
                name = (p.player_name or p.primary_id).strip() or p.primary_id
                consider(
                    ratio,
                    f"{name} is well below your team's median {label} ({_fmt(stat_key, v)} vs {_fmt(stat_key, med)}).",
                )

    stat_key = "shooting_percentage"
    values = [float(p.stats.get(stat_key, 0.0)) for p in lobby]
    med = _median(values)
    if med > 1e-6:
        label = STAT_LABELS.get(stat_key, stat_key)
        for p, v in zip(lobby, values, strict=True):
            if v > med and v / max(med, 1e-6) >= OUTLIER_RATIO:
                ratio = v / max(med, 1e-6)
                name = (p.player_name or p.primary_id).strip() or p.primary_id
                consider(
                    ratio,
                    f"{name} is far above your team's median {label} ({_fmt(stat_key, v)} vs {_fmt(stat_key, med)}).",
                )


def _team_outliers(
    teams: tuple[_TeamStatsLike, ...],
    consider: Callable[[float, str], None],
) -> None:
    if len(teams) != 2:
        return
    a, b = teams[0], teams[1]
    for stat_key in ("demos_inflicted", "time_zero_boost", "shooting_percentage", "avg_boost"):
        va = float(a.stats.get(stat_key, 0.0))
        vb = float(b.stats.get(stat_key, 0.0))
        if va > 1e-6 and vb > 1e-6:
            if va >= vb * OUTLIER_RATIO:
                consider(
                    va / max(vb, 1e-6),
                    f"{a.team_name} has much more {STAT_LABELS.get(stat_key, stat_key)} than {b.team_name} "
                    f"({_fmt(stat_key, va)} vs {_fmt(stat_key, vb)}).",
                )
            elif vb >= va * OUTLIER_RATIO:
                consider(
                    vb / max(va, 1e-6),
                    f"{b.team_name} has much more {STAT_LABELS.get(stat_key, stat_key)} than {a.team_name} "
                    f"({_fmt(stat_key, vb)} vs {_fmt(stat_key, va)}).",
                )


def _fmt(stat_key: str, value: float) -> str:
    if stat_key.startswith("time_"):
        return f"{value:.1f}s"
    if "percentage" in stat_key:
        return f"{value:.1f}%"
    if stat_key.startswith("avg_"):
        return f"{value:.1f}"
    return f"{value:.0f}"
