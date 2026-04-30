from __future__ import annotations

from demolytics.domain.stats import SUPPORTED_STAT_KEYS

_DURATION_EPS = 1e-3

# Stats stored as match-long percentages or time-weighted means — comparable across match lengths.
_AS_STORED_KEYS = frozenset(
    {
        "shooting_percentage",
        "airborne_percentage",
        "avg_boost",
        "avg_speed",
    }
)


def normalize_stats_for_goal_insight(stats: dict[str, float], duration_seconds: float) -> dict[str, float]:
    """Map raw per-match stats to comparable units (rates or fractions) for rolling averages."""
    d = max(float(duration_seconds), _DURATION_EPS)
    out: dict[str, float] = {}
    for key in SUPPORTED_STAT_KEYS:
        v = float(stats.get(key, 0.0))
        if key in _AS_STORED_KEYS:
            out[key] = v
        elif key.startswith("time_"):
            out[key] = v / d
        else:
            out[key] = v / (d / 60.0)
    return out


def denormalize_stat_for_display(stat_key: str, normalized: float, duration_seconds: float) -> float:
    """Turn a normalized baseline into the same units as live cumulative stats at ``duration_seconds``."""
    d = max(float(duration_seconds), _DURATION_EPS)
    if stat_key in _AS_STORED_KEYS:
        return normalized
    if stat_key.startswith("time_"):
        return normalized * d
    return normalized * (d / 60.0)


def average_normalized_rows(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {k: 0.0 for k in SUPPORTED_STAT_KEYS}
    acc = {k: 0.0 for k in SUPPORTED_STAT_KEYS}
    for row in rows:
        for k in SUPPORTED_STAT_KEYS:
            acc[k] += float(row.get(k, 0.0))
    n = float(len(rows))
    return {k: acc[k] / n for k in SUPPORTED_STAT_KEYS}
