from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from demolytics.domain.goal_insight_stats import (
    denormalize_stat_for_display,
    normalize_stats_for_goal_insight,
)
from demolytics.domain.stats import STAT_LABELS

# Floor for "enough" match time so player stats are comparable (in-game clock or accumulated).
MIN_LOBBY_SECONDS = 4.0
# Completed matches in this playlist before historical self-baselines apply (new installs stay lobby-only).
MIN_HISTORICAL_MATCHES = 5
# Opponent stat rows aggregated across those matches; below this, skip historical_opponents (noisy / thin data).
MIN_HISTORICAL_OPPONENT_SAMPLES = 5
# Minimum smoothed separation max((uv+1)/(med+1), (med+1)/(uv+1)) to count as an outlier.
OUTLIER_SEPARATION = 1.5

# Rocket League often omits on-ground state for other cars; without ground time, airborne % is meaningless.
MIN_VISIBLE_GROUND_SECONDS = 0.5

# Opponent cars often lack real Boost in the plugin payload; comparing these vs opponents is misleading.
BOOST_DERIVED_STAT_KEYS = frozenset({"avg_boost"})

# Integer stats: treat ±1–2 demo differences as noise (e.g. 2 vs 1); require gap > this threshold.
MIN_DEMO_COUNT_GAP_FOR_OUTLIER = 2

# Opponent row looks like missing boost telemetry (do not use for boost-derived medians / fallbacks).
def _opponent_boost_row_unreliable(p: _PlayerStatsLike, user_team: int) -> bool:
    if p.team_num == user_team:
        return False
    return float(p.stats.get("avg_boost", 0.0)) <= 1e-6


def _opponent_speed_row_unreliable(p: _PlayerStatsLike, user_team: int) -> bool:
    if p.team_num == user_team:
        return False
    return float(p.stats.get("avg_speed", 0.0)) <= 1e-3


_FALLBACK_STAT_KEYS = (
    "avg_boost",
    "possession_percentage",
    "avg_speed",
    "demos_inflicted",
    "demos_taken",
    "airborne_percentage",
)


PeerGroupLiteral = Literal[
    "teammates",
    "opponents",
    "historical_self",
    "historical_opponents",
]


@dataclass(frozen=True)
class HistoricalBaselines:
    """Playlist-segregated rolling baselines for goal insights (normalized rates)."""

    user_rates: dict[str, float]
    opponent_rates: dict[str, float]
    n_matches: int
    n_opponent_samples: int


@dataclass(frozen=True)
class GoalInsightResult:
    """Structured outcome of after-goal insight selection (for UI + analytics)."""

    message: str
    stat_key: str
    kind: Literal["outlier", "fallback"]
    peer_group: PeerGroupLiteral
    user_value: float
    peer_median: float
    # For outliers: smoothed separation score (max of upward/downward ratios). Fallback: None.
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


def _demo_outlier_gap_ok(stat_key: str, uv: float, med: float) -> bool:
    if stat_key not in ("demos_inflicted", "demos_taken"):
        return True
    return abs(int(round(uv)) - int(round(med))) > MIN_DEMO_COUNT_GAP_FOR_OUTLIER


def _smoothed_separation(
    uv: float, med: float
) -> tuple[float, Literal["user_above_peer", "user_below_peer"]]:
    """Symmetric (+1) ratio; direction follows which side dominates."""
    ratio_up = (uv + 1.0) / (med + 1.0)
    ratio_down = (med + 1.0) / (uv + 1.0)
    if ratio_up >= ratio_down:
        return ratio_up, "user_above_peer"
    return ratio_down, "user_below_peer"


def _lobby_peers_excluding_user(
    players: tuple[_PlayerStatsLike, ...], user: _PlayerStatsLike
) -> tuple[_PlayerStatsLike, ...]:
    return tuple(p for p in players if p.primary_id and not p.is_user)


def _fallback_eligible_peer_values(
    stat_key: str,
    user: _PlayerStatsLike,
    lobby_peers: tuple[_PlayerStatsLike, ...],
) -> list[float] | None:
    """Peer values for lobby median, or None if this stat is not comparable for the lobby."""
    if stat_key == "airborne_percentage" and not _airborne_comparison_trustworthy(user, lobby_peers):
        return None
    out: list[float] = []
    for p in lobby_peers:
        if stat_key in BOOST_DERIVED_STAT_KEYS and _opponent_boost_row_unreliable(p, user.team_num):
            continue
        if stat_key == "avg_speed" and _opponent_speed_row_unreliable(p, user.team_num):
            continue
        out.append(float(p.stats.get(stat_key, 0.0)))
    return out if out else None


def compute_goal_insight(
    players: tuple[_PlayerStatsLike, ...],
    match_duration_seconds: float,
    *,
    insight_salt: int = 0,
    historical: HistoricalBaselines | None = None,
) -> GoalInsightResult | None:
    _ = insight_salt  # retained for API compatibility; fallback uses closest-to-lobby, not rotation.
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

    live_norm = normalize_stats_for_goal_insight(user.stats, match_duration_seconds)
    effective_historical = (
        historical
        if historical is not None and historical.n_matches >= MIN_HISTORICAL_MATCHES
        else None
    )

    best_ratio = 0.0
    best_message: str | None = None
    best_stat_key: str | None = None
    best_uv = 0.0
    best_med = 0.0
    best_direction: Literal["user_above_peer", "user_below_peer"] | None = None
    best_peer_group: PeerGroupLiteral | None = None

    def consider(
        ratio: float,
        message: str,
        *,
        stat_key: str,
        uv: float,
        med: float,
        direction: Literal["user_above_peer", "user_below_peer"],
        peer_group: PeerGroupLiteral,
    ) -> None:
        nonlocal best_ratio, best_message, best_stat_key, best_uv, best_med, best_direction, best_peer_group
        if ratio > best_ratio and ratio >= OUTLIER_SEPARATION:
            best_ratio = ratio
            best_message = message
            best_stat_key = stat_key
            best_uv = uv
            best_med = med
            best_direction = direction
            best_peer_group = peer_group

    if teammates:
        _user_vs_peer_median_outliers(user, teammates, consider, peer_group="teammates")
    if opponents:
        _user_vs_peer_median_outliers(user, opponents, consider, peer_group="opponents")

    _historical_outliers(
        user,
        effective_historical,
        consider,
        live_norm,
        match_duration_seconds,
    )

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

    lobby = _lobby_peers_excluding_user(players, user)
    fb = _fallback_user_insight(user, lobby)
    if fb is not None:
        return fb
    return _historical_fallback_insight(
        user, effective_historical, live_norm, match_duration_seconds
    )


def _historical_outliers(
    user: _PlayerStatsLike,
    historical: HistoricalBaselines | None,
    consider: Callable[..., None],
    live_norm: dict[str, float],
    match_duration_seconds: float,
) -> None:
    if historical is None or historical.n_matches <= 0:
        return
    for stat_key in (
        "possession_percentage",
        "demos_inflicted",
        "demos_taken",
        "avg_boost",
        "avg_speed",
        "airborne_percentage",
    ):
        uv_raw = float(user.stats.get(stat_key, 0.0))
        uv_n = float(live_norm.get(stat_key, 0.0))
        for baseline, peer_group, peer_phrase in (
            (historical.user_rates, "historical_self", "your recent average in this playlist"),
            (historical.opponent_rates, "historical_opponents", "the recent average of opponents you've faced"),
        ):
            if peer_group == "historical_opponents":
                if historical.n_opponent_samples < MIN_HISTORICAL_OPPONENT_SAMPLES:
                    continue
                if stat_key in BOOST_DERIVED_STAT_KEYS:
                    continue
            med_n = float(baseline.get(stat_key, 0.0))
            sep, direction = _smoothed_separation(uv_n, med_n)
            med_display = denormalize_stat_for_display(stat_key, med_n, match_duration_seconds)
            if not _demo_outlier_gap_ok(stat_key, uv_raw, med_display):
                continue
            if sep < OUTLIER_SEPARATION:
                continue
            label = STAT_LABELS.get(stat_key, stat_key)
            ref = f"median {_fmt(stat_key, med_display)}" if peer_group == "historical_opponents" else f"your {_fmt(stat_key, med_display)}"
            if direction == "user_above_peer":
                consider(
                    sep,
                    f"You have much more {label} than {peer_phrase} "
                    f"({_fmt(stat_key, uv_raw)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv_raw,
                    med=med_display,
                    direction=direction,
                    peer_group=peer_group,
                )
            else:
                consider(
                    sep,
                    f"Your {label} is well below {peer_phrase} "
                    f"({_fmt(stat_key, uv_raw)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv_raw,
                    med=med_display,
                    direction=direction,
                    peer_group=peer_group,
                )


def _historical_fallback_insight(
    user: _PlayerStatsLike,
    historical: HistoricalBaselines | None,
    live_norm: dict[str, float],
    match_duration_seconds: float,
) -> GoalInsightResult | None:
    if historical is None or historical.n_matches <= 0:
        return None
    best: tuple[float, str, str, float, float, PeerGroupLiteral] | None = None
    for stat_key in sorted(_FALLBACK_STAT_KEYS):
        uv_raw = float(user.stats.get(stat_key, 0.0))
        uv_n = float(live_norm.get(stat_key, 0.0))
        for med_n, peer_group, label_who in (
            (float(historical.user_rates.get(stat_key, 0.0)), "historical_self", "your recent average in this playlist"),
            (
                float(historical.opponent_rates.get(stat_key, 0.0)),
                "historical_opponents",
                "the recent average of opponents you've faced",
            ),
        ):
            if peer_group == "historical_opponents":
                if historical.n_opponent_samples < MIN_HISTORICAL_OPPONENT_SAMPLES:
                    continue
                if stat_key in BOOST_DERIVED_STAT_KEYS:
                    continue
            med_display = denormalize_stat_for_display(stat_key, med_n, match_duration_seconds)
            dist = abs(uv_n - med_n)
            cand = (dist, stat_key, label_who, uv_raw, med_display, peer_group)
            if best is None or cand < best:
                best = cand
    if best is None:
        return None
    _, stat_key, label_who, uv, med, peer_group = best
    label = STAT_LABELS.get(stat_key, stat_key)
    msg = (
        f"Your {label.lower()} ({_fmt(stat_key, uv)}) is close to {label_who} "
        f"({_fmt(stat_key, med)})."
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


def _emit_outlier_for_stat(
    stat_key: str,
    user: _PlayerStatsLike,
    peers: tuple[_PlayerStatsLike, ...],
    med: float,
    uv: float,
    consider: Callable[..., None],
    *,
    peer_group: Literal["teammates", "opponents"],
    peer_phrase: str,
) -> None:
    sep, direction = _smoothed_separation(uv, med)
    if sep < OUTLIER_SEPARATION:
        return
    if not _demo_outlier_gap_ok(stat_key, uv, med):
        return
    label = STAT_LABELS.get(stat_key, stat_key)
    ref = _peer_baseline_label(peers, stat_key, med)
    if direction == "user_above_peer":
        consider(
            sep,
            f"You have much more {label} than {peer_phrase} so far "
            f"({_fmt(stat_key, uv)} vs {ref}).",
            stat_key=stat_key,
            uv=uv,
            med=med,
            direction=direction,
            peer_group=peer_group,
        )
    else:
        consider(
            sep,
            f"Your {label} is well below {peer_phrase} "
            f"({_fmt(stat_key, uv)} vs {ref}).",
            stat_key=stat_key,
            uv=uv,
            med=med,
            direction=direction,
            peer_group=peer_group,
        )


def _user_vs_peer_median_outliers(
    user: _PlayerStatsLike,
    peers: tuple[_PlayerStatsLike, ...],
    consider: Callable[..., None],
    *,
    peer_group: Literal["teammates", "opponents"],
) -> None:
    same_team = peer_group == "teammates"
    peer_phrase = _peer_high_phrase(same_team, len(peers))

    for stat_key in ("demos_inflicted", "demos_taken", "possession_percentage", "avg_speed"):
        if peer_group == "opponents" and stat_key in BOOST_DERIVED_STAT_KEYS:
            continue
        values: list[float] = []
        for p in peers:
            if stat_key == "avg_speed" and peer_group == "opponents" and _opponent_speed_row_unreliable(
                p, user.team_num
            ):
                continue
            values.append(float(p.stats.get(stat_key, 0.0)))
        if not values:
            continue
        med = _median(values)
        uv = float(user.stats.get(stat_key, 0.0))
        _emit_outlier_for_stat(
            stat_key, user, peers, med, uv, consider, peer_group=peer_group, peer_phrase=peer_phrase
        )

    stat_key = "airborne_percentage"
    if _airborne_comparison_trustworthy(user, peers):
        values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
        med = _median(values)
        uv = float(user.stats.get(stat_key, 0.0))
        _emit_outlier_for_stat(
            stat_key, user, peers, med, uv, consider, peer_group=peer_group, peer_phrase=peer_phrase
        )

    if peer_group != "opponents":
        stat_key = "avg_boost"
        values = [float(p.stats.get(stat_key, 0.0)) for p in peers]
        med = _median(values)
        uv = float(user.stats.get(stat_key, 0.0))
        sep, direction = _smoothed_separation(uv, med)
        if sep >= OUTLIER_SEPARATION:
            label = STAT_LABELS.get(stat_key, stat_key)
            ref = _peer_baseline_label(peers, stat_key, med)
            if direction == "user_above_peer":
                consider(
                    sep,
                    f"Your {label} is far above {peer_phrase} "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction=direction,
                    peer_group=peer_group,
                )
            else:
                consider(
                    sep,
                    f"Your {label} is well below {peer_phrase} "
                    f"({_fmt(stat_key, uv)} vs {ref}).",
                    stat_key=stat_key,
                    uv=uv,
                    med=med,
                    direction=direction,
                    peer_group=peer_group,
                )


def _fallback_user_insight(
    user: _PlayerStatsLike,
    lobby_peers: tuple[_PlayerStatsLike, ...],
) -> GoalInsightResult | None:
    if not lobby_peers:
        return None

    best: tuple[float, str, float, float] | None = None
    for stat_key in sorted(_FALLBACK_STAT_KEYS):
        eligible = _fallback_eligible_peer_values(stat_key, user, lobby_peers)
        if not eligible:
            continue
        med = _median(eligible)
        uv = float(user.stats.get(stat_key, 0.0))
        if med <= 1e-6 and uv <= 1e-6:
            continue
        dist = abs(uv - med)
        cand = (dist, stat_key, uv, med)
        if best is None or cand[0] < best[0] or (cand[0] == best[0] and cand[1] < best[1]):
            best = cand

    if best is None:
        return None
    _, stat_key, uv, med = best
    label = STAT_LABELS.get(stat_key, stat_key)
    n_opp = sum(1 for p in lobby_peers if p.team_num != user.team_num)
    n_mate = len(lobby_peers) - n_opp
    if n_opp == 0:
        peer_group: PeerGroupLiteral = "teammates"
        med_note = (
            f"median {_fmt(stat_key, med)}" if n_mate > 1 else f"their {_fmt(stat_key, med)}"
        )
        who = "teammates'" if n_mate > 1 else "teammate's"
        msg = (
            f"Your {label.lower()} ({_fmt(stat_key, uv)}) is close to your {who} "
            f"typical level ({med_note})."
        )
    elif n_mate == 0:
        peer_group = "opponents"
        if n_opp == 1:
            msg = (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is comparable to your opponent's "
                f"({_fmt(stat_key, med)})."
            )
        else:
            msg = (
                f"Your {label.lower()} ({_fmt(stat_key, uv)}) is near the rest of the lobby's "
                f"typical mark (median {_fmt(stat_key, med)})."
            )
    else:
        peer_group = "teammates"
        msg = (
            f"Your {label.lower()} ({_fmt(stat_key, uv)}) is near the lobby's "
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


def _fmt(stat_key: str, value: float) -> str:
    if stat_key.startswith("time_"):
        return f"{value:.1f}s"
    if "percentage" in stat_key:
        return f"{value:.1f}%"
    if stat_key.startswith("avg_"):
        return f"{value:.1f}"
    return f"{value:.0f}"
