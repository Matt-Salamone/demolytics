from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatDefinition:
    key: str
    label: str
    unit: str = ""
    supported: bool = True


STAT_DEFINITIONS: tuple[StatDefinition, ...] = (
    StatDefinition("score", "Score"),
    StatDefinition("goals", "Goals"),
    StatDefinition("assists", "Assists"),
    StatDefinition("saves", "Saves"),
    StatDefinition("shots", "Shots"),
    StatDefinition("touches", "Touches"),
    StatDefinition("shooting_percentage", "Shooting %", "%"),
    StatDefinition("goals_conceded", "Goals Conceded"),
    StatDefinition("demos_inflicted", "Demos Inflicted"),
    StatDefinition("demos_taken", "Demos Taken"),
    StatDefinition("avg_boost", "Average Boost"),
    StatDefinition("time_zero_boost", "Time at 0 Boost", "s"),
    StatDefinition("time_full_boost", "Time at 100 Boost", "s"),
    StatDefinition("avg_speed", "Average Speed"),
    StatDefinition("time_boosting", "Time Boosting", "s"),
    StatDefinition("time_supersonic", "Time Supersonic", "s"),
    StatDefinition("time_powerslide", "Time Powerslide", "s"),
    StatDefinition("time_on_ground", "Time on Ground", "s"),
    StatDefinition("time_airborne", "Time Airborne", "s"),
    StatDefinition("airborne_percentage", "Airborne %", "%"),
    StatDefinition("avg_dist_to_ball", "Average Distance to Ball", supported=False),
    StatDefinition("time_behind_ball", "Time Behind Ball", supported=False),
    StatDefinition("time_in_front_of_ball", "Time In Front of Ball", supported=False),
    StatDefinition("time_def_half", "Time Defensive Half", supported=False),
    StatDefinition("time_off_half", "Time Offensive Half", supported=False),
    StatDefinition("pads_collected_big", "Big Pads Collected", supported=False),
    StatDefinition("pads_collected_small", "Small Pads Collected", supported=False),
    StatDefinition("boost_stolen", "Boost Stolen", supported=False),
)

SUPPORTED_STAT_KEYS: tuple[str, ...] = tuple(
    stat.key for stat in STAT_DEFINITIONS if stat.supported
)

STAT_LABELS: dict[str, str] = {stat.key: stat.label for stat in STAT_DEFINITIONS}
