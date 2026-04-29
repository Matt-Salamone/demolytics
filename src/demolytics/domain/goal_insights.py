from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from demolytics.domain.stats import STAT_LABELS

# Floor for "enough" match time so player stats are comparable (in-game clock or accumulated).
MIN_LOBBY_SECONDS = 4.0
OUTLIER_RATIO = 1.5

# Rocket League often omits on-ground state for other cars; without ground time, airborne % is meaningless.
MIN_VISIBLE_GROUND_SECONDS = 0.5

# Snapshot reports 0% shooting when shots==0; that is not comparable to a real percentage.
MIN_SHOTS_FOR_SHOOTING_COMPARISON = 1

# Opponent cars often lack real Boost in the plugin payload; comparing these vs opponents is misleading.
BOOST_DERIVED_STAT_KEYS = frozenset(
    {"avg_boost", "time_zero_boost", "time_full_boost", "time_boosting"}
)

# Integer stats: ratio-only outliers read as dramatic for ±1 (e.g. 2 vs 1 demos).
MIN_DEMO_COUNT_GAP_FOR_OUTLIER = 2

# Rotated when no outlier fires; first usable stat wins.
_FALLBACK_STAT_KEYS = (
    "avg_boost",
    "time_zero_boost",
    "shooting_percentage",
    "demos_inflicted",
    "demos_taken",
    "airborne_percentage",
)


@dataclass(frozen=True)
class GoalInsightResult:
    """Structured outcome of after-goal insight selection (for UI + analytics)."""

    message: str
    stat_key: str
    kind: Literal["outlier", "fallback"]
    peer_group: Literal["teammates", "opponents"]
    user_value: float
    peer_median: float
    ratio: float | None
    outlier_direction: Literal["user_above_peer", "user_below_peer"] | None


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


def _shooting_comparison_trustworthy(
    user: _PlayerStatsLike, peers: tuple[_PlayerStatsLike, ...]
) -> bool:
    """Only compare shooting % when everyone has at least one shot (otherwise RL reports 0%)."""
    if float(user.stats.get("shots", 0.0)) < MIN_SHOTS_FOR_SHOOTING_COMPARISON:
        return False
    return all(float(p.stats.get("shots", 0.0)) >= MIN_SHOTS_FOR_SHOOTING_COMPARISON for p in peers)


def _demo_outlier_gap_ok(stat_key: str, uv: float, med: float) -> bool:
    if stat_key not in ("demos_inflicted", "demos_taken"):
        return True
    return abs(int(round(uv)) - int(round(med))) >= MIN_DEMO_COUNT_GAP_FOR_OUTLIER


def compute_goal_insight(
    players: tuple[_PlayerStatsLike, ...],
    match_duration_seconds: float,
    *,
    insight_salt: int = 0,
) -> GoalInsightResult | None:
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
    best_stat_key: str | None = None
    best_uv = 0.0
    best_med = 0.0
    best_direction: Literal["user_above_peer", "user_below_peer"] | None = None
    best_peer_group: Literal["teammates", "opponents"] | None = None

    def consider(
        ratio: float,
        message: str,
        *,
        stat_key: str,
        uv: float,
        med: float,
        direction: Literal["user_above_peer", "user_below_peer"],
        peer_group: Literal["teammates", "opponents"],
    ) -> None:
        nonlocal best_ratio, best_message, best_stat_key, best_uv, best_med, best_direction, best_peer_group
        if ratio > best_ratio and ratio >= OUTLIER_RATIO:
            best_ratio = ratio
            best_message = message
            best_stat_key = stat_key
            best_uv = uv
            best_med = med
            best_direction = direction
            best_peer_group = peer_group

    if teammates:
        _user_vs_peer_median_outliers(user, teammates, consider, peer_group="teammates")
    elif opponents:
        _user_vs_peer_median_outliers(user, opponents, consider, peer_group="opponents")

    if best_message is not None and best_stat_key is not None and best_peer_group is not None:
        return GoalInsightResult(
            message=best_message,
            stat_key=best_stat_key,
            kind="outlier",
            peer_group=best_peer_group,
            user_value=best_uv,
            peer_median=best_med,
            ratio=best_ratio,
            outlier_direction=best_direction,
        )

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
    consider: Callable[..., None],
    *,
    peer_group: Literal["teammates", "opponents"],
) -> None:
    same_team = peer_group == "teammates"
    peer_phrase = _peer_high_phrase(same_team, len(peers))

    for stat_key, higher_is_notable in (
        ("time_zero_boost", True),
        ("demos_inflicted", True),
        ("demos_taken", True),
        ("airborne_percentage", True),
    ):
        if peer_group == "opponents" and stat_key in BOOST_DERIVED_STAT_KEYS:
            continue
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
            demo_gap = _demo_outlier_gap_ok(stat_key, uv, med)
            if uv / med >= OUTLIER_RATIO and demo_gap:
                consider(
                    uv / med,
                    f"You have much more {label} than {peer_phrase} so far "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction="user_above_peer",
                    peer_group=peer_group,
                )
            elif med / max(uv, 1e-6) >= OUTLIER_RATIO and demo_gap:
                consider(
                    med / max(uv, 1e-6),
                    f"Your {label} is well below {peer_phrase} "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction="user_below_peer",
                    peer_group=peer_group,
                )

    if peer_group != "opponents":
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
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction="user_below_peer",
                    peer_group=peer_group,
                )

    stat_key = "shooting_percentage"
    if _shooting_comparison_trustworthy(user, peers):
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
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction="user_above_peer",
                    peer_group=peer_group,
                )


def _fallback_user_insight(
    user: _PlayerStatsLike,
    teammates: tuple[_PlayerStatsLike, ...],
    opponents: tuple[_PlayerStatsLike, ...],
    insight_salt: int,
) -> GoalInsightResult | None:
    if teammates:
        peers = teammates
        peer_label = "teammates"
        peer_group: Literal["teammates", "opponents"] = "teammates"
    elif opponents:
        peers = opponents
        peer_label = "opponent" if len(opponents) == 1 else "the other team"
        peer_group = "opponents"
    else:
        return None

    start = abs(insight_salt) % len(_FALLBACK_STAT_KEYS)
    for offset in range(len(_FALLBACK_STAT_KEYS)):
        stat_key = _FALLBACK_STAT_KEYS[(start + offset) % len(_FALLBACK_STAT_KEYS)]
        if peer_group == "opponents" and stat_key in BOOST_DERIVED_STAT_KEYS:
            continue
        if stat_key == "airborne_percentage" and not _airborne_comparison_trustworthy(user, peers):
            continue
        if stat_key == "shooting_percentage" and not _shooting_comparison_trustworthy(user, peers):
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
            msg = (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is close to your {who} "
                f"typical level ({med_note})."
            )
        elif peer_label == "opponent":
            msg = (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is comparable to your opponent's "
                f"({_fmt(stat_key, med)})."
            )
        else:
            msg = (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is near the rest of the lobby's "
                f"typical mark (median {_fmt(stat_key, med)})."
            )
        return GoalInsightResult(
            message=msg,
            stat_key=stat_key,
            kind="fallback",
            peer_group=peer_group,
            user_value=uv,
            peer_median=med,
            ratio=None,
            outlier_direction=None,
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
