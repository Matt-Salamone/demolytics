from __future__ import annotations

from typing import Callable, Protocol

from demolytics.domain.stats import STAT_LABELS

# Floor for "enough" match time so player stats are comparable (in-game clock or accumulated).
MIN_LOBBY_SECONDS = 4.0
OUTLIER_RATIO = 1.5

# Rocket League often omits on-ground state for other cars; without ground time, airborne % is meaningless.
MIN_VISIBLE_GROUND_SECONDS = 0.5

# Rotated when no outlier fires; first usable stat wins.
_FALLBACK_STAT_KEYS = (
    "avg_boost",
    "time_zero_boost",
    "shooting_percentage",
    "demos_inflicted",
    "demos_taken",
    "airborne_percentage",
)


class _PlayerStatsLike(Protocol):
    primary_id: str
    player_name: str
    team_num: int
    is_user: bool
    stats: dict[str, float]


def _airborne_stat_visible(p: _PlayerStatsLike) -> bool:
    """True when API has reported wheel contact / on-ground time for this car."""
    return float(p.stats.get("time_on_ground", 0.0)) >= MIN_VISIBLE_GROUND_SECONDS


def _airborne_comparison_trustworthy(
    user: _PlayerStatsLike, peers: tuple[_PlayerStatsLike, ...]
) -> bool:
    """Only compare airborne % when ground time exists for everyone in the comparison."""
    if not _airborne_stat_visible(user):
        return False
    return all(_airborne_stat_visible(p) for p in peers)


def compute_goal_insight(
    players: tuple[_PlayerStatsLike, ...],
    match_duration_seconds: float,
    *,
    insight_salt: int = 0,
) -> str | None:
    distinct = {p.primary_id for p in players if p.primary_id}
    if len(distinct) < 2:
        return None
    if match_duration_seconds < MIN_LOBBY_SECONDS:
        return None

    user = next((p for p in players if p.is_user), None)
    if user is None or not user.primary_id:
        return None

    teammates = tuple(
        p for p in players if p.primary_id and p.team_num == user.team_num and not p.is_user
    )
    opponents = tuple(p for p in players if p.primary_id and p.team_num != user.team_num)

    best_ratio = 0.0
    best_message: str | None = None

    def consider(ratio: float, message: str) -> None:
        nonlocal best_ratio, best_message
        if ratio > best_ratio and ratio >= OUTLIER_RATIO:
            best_ratio = ratio
            best_message = message

    if teammates:
        _user_vs_peer_median_outliers(user, teammates, consider, same_team=True)
    elif opponents:
        _user_vs_peer_median_outliers(user, opponents, consider, same_team=False)

    if best_message is not None:
        return best_message

    return _fallback_user_insight(user, teammates, opponents, insight_salt)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _peer_baseline_label(peers: tuple[_PlayerStatsLike, ...], stat_key: str, med: float) -> str:
    if len(peers) == 1:
        return f"their {_fmt(stat_key, med)}"
    return f"median {_fmt(stat_key, med)}"


def _peer_high_phrase(same_team: bool, num_peers: int) -> str:
    if same_team:
        return "your teammates' typical level" if num_peers > 1 else "your teammate's typical level"
    return "your opponent" if num_peers == 1 else "the other team's typical level"


def _user_vs_peer_median_outliers(
    user: _PlayerStatsLike,
    peers: tuple[_PlayerStatsLike, ...],
    consider: Callable[[float, str], None],
    *,
    same_team: bool,
) -> None:
    peer_phrase = _peer_high_phrase(same_team, len(peers))

    for stat_key, higher_is_notable in (
        ("time_zero_boost", True),
        ("demos_inflicted", True),
        ("demos_taken", True),
        ("airborne_percentage", True),
    ):
        if stat_key == "airborne_percentage" and not _airborne_comparison_trustworthy(user, peers):
            continue
        values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
        med = _median(values)
        if med <= 1e-6:
            continue
        label = STAT_LABELS.get(stat_key, stat_key)
        uv = float(user.stats.get(stat_key, 0.0))
        ref = _peer_baseline_label(peers, stat_key, med)
        if higher_is_notable:
            if uv / med >= OUTLIER_RATIO:
                consider(
                    uv / med,
                    f"You have much more {label} than {peer_phrase} so far "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                )
            elif med / max(uv, 1e-6) >= OUTLIER_RATIO:
                consider(
                    med / max(uv, 1e-6),
                    f"Your {label} is well below {peer_phrase} "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                )

    stat_key = "avg_boost"
    values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
    med = _median(values)
    if med > 1e-6:
        label = STAT_LABELS.get(stat_key, stat_key)
        uv = float(user.stats.get(stat_key, 0.0))
        ref = _peer_baseline_label(peers, stat_key, med)
        if uv < med and med / max(uv, 1e-6) >= OUTLIER_RATIO:
            consider(
                med / max(uv, 1e-6),
                f"Your {label} is well below {peer_phrase} "
                f"({_fmt(stat_key, uv)} vs {ref}).",
            )

    stat_key = "shooting_percentage"
    values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
    med = _median(values)
    if med > 1e-6:
        label = STAT_LABELS.get(stat_key, stat_key)
        uv = float(user.stats.get(stat_key, 0.0))
        ref = _peer_baseline_label(peers, stat_key, med)
        if uv > med and uv / max(med, 1e-6) >= OUTLIER_RATIO:
            consider(
                uv / max(med, 1e-6),
                f"Your {label} is far above {peer_phrase} "
                f"({_fmt(stat_key, uv)} vs {ref}).",
            )


def _fallback_user_insight(
    user: _PlayerStatsLike,
    teammates: tuple[_PlayerStatsLike, ...],
    opponents: tuple[_PlayerStatsLike, ...],
    insight_salt: int,
) -> str | None:
    if teammates:
        peers = teammates
        peer_label = "teammates"
    elif opponents:
        peers = opponents
        peer_label = "opponent" if len(opponents) == 1 else "the other team"
    else:
        return None

    start = abs(insight_salt) % len(_FALLBACK_STAT_KEYS)
    for offset in range(len(_FALLBACK_STAT_KEYS)):
        stat_key = _FALLBACK_STAT_KEYS[(start + offset) % len(_FALLBACK_STAT_KEYS)]
        if stat_key == "airborne_percentage" and not _airborne_comparison_trustworthy(user, peers):
            continue
        values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
        med = _median(values)
        uv = float(user.stats.get(stat_key, 0.0))
        label = STAT_LABELS.get(stat_key, stat_key)
        if med <= 1e-6 and uv <= 1e-6:
            continue
        if peer_label == "teammates":
            med_note = (
                f"median {_fmt(stat_key, med)}" if len(peers) > 1 else f"their {_fmt(stat_key, med)}"
            )
            who = "teammates'" if len(peers) > 1 else "teammate's"
            return (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is close to your {who} "
                f"typical level ({med_note})."
            )
        if peer_label == "opponent":
            return (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is comparable to your opponent's "
                f"({_fmt(stat_key, med)})."
            )
        return (
            f"Your {label.lower()} ({_fmt(stat_key, uv)}) is near the rest of the lobby's "
            f"typical mark (median {_fmt(stat_key, med)})."
        )
    return None


def _fmt(stat_key: str, value: float) -> str:
    if stat_key.startswith("time_"):
        return f"{value:.1f}s"
    if "percentage" in stat_key:
        return f"{value:.1f}%"
    if stat_key.startswith("avg_"):
        return f"{value:.1f}"
    return f"{value:.0f}"
