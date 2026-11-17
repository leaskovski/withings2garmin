"""Pure helpers that turn Withings measure groups into Garmin payloads.

This module deliberately has no Home Assistant or network dependencies so it
can be unit tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Withings measure types
# https://developer.withings.com/api-reference/#tag/measure/operation/measure-getmeas
MEAS_WEIGHT = 1  # kg
MEAS_FAT_RATIO = 6  # %
MEAS_DIASTOLIC = 9  # mmHg
MEAS_SYSTOLIC = 10  # mmHg
MEAS_HEART_PULSE = 11  # bpm
MEAS_MUSCLE_MASS = 76  # kg
MEAS_HYDRATION = 77  # kg (mass of body water, not a percentage)
MEAS_BONE_MASS = 88  # kg
MEAS_VISCERAL_FAT = 170  # index

BODY_COMPOSITION_TYPES = (
    MEAS_WEIGHT,
    MEAS_FAT_RATIO,
    MEAS_MUSCLE_MASS,
    MEAS_HYDRATION,
    MEAS_BONE_MASS,
    MEAS_VISCERAL_FAT,
)
BLOOD_PRESSURE_TYPES = (MEAS_DIASTOLIC, MEAS_SYSTOLIC, MEAS_HEART_PULSE)

# Ranges accepted by garminconnect's set_blood_pressure()
_SYSTOLIC_RANGE = (70, 260)
_DIASTOLIC_RANGE = (40, 150)
_PULSE_RANGE = (20, 250)


@dataclass(frozen=True)
class MeasureGroup:
    """A single Withings measure group (one weigh-in / one BP reading)."""

    grpid: int
    date: int  # epoch seconds when the measurement was taken
    modified: int  # epoch seconds when the group was last modified
    values: dict[int, float]

    @property
    def timestamp(self) -> datetime:
        """Measurement time as an aware UTC datetime."""
        return datetime.fromtimestamp(self.date, tz=timezone.utc)


@dataclass(frozen=True)
class BodyComposition:
    """Payload for Garmin add_body_composition()."""

    timestamp: datetime
    weight: float
    percent_fat: float | None = None
    percent_hydration: float | None = None
    bone_mass: float | None = None
    muscle_mass: float | None = None
    visceral_fat_rating: float | None = None


@dataclass(frozen=True)
class BloodPressure:
    """Payload for Garmin set_blood_pressure()."""

    timestamp: datetime
    systolic: int
    diastolic: int
    pulse: int | None = None


def decode_value(measure: dict[str, Any]) -> float:
    """Decode a Withings measure: real value = value * 10^unit."""
    value = float(measure["value"])
    unit = int(measure["unit"])
    # Divide for negative exponents to avoid float noise (72350 * 0.001 != 72.35)
    return value / (10 ** -unit) if unit < 0 else value * (10**unit)


def parse_groups(measuregrps: list[dict[str, Any]]) -> list[MeasureGroup]:
    """Parse the `measuregrps` list of a getmeas response."""
    groups: list[MeasureGroup] = []
    for grp in measuregrps:
        values: dict[int, float] = {}
        for measure in grp.get("measures", []):
            try:
                values[int(measure["type"])] = decode_value(measure)
            except (KeyError, TypeError, ValueError):
                continue
        date = int(grp.get("date", 0))
        groups.append(
            MeasureGroup(
                grpid=int(grp.get("grpid", 0)),
                date=date,
                modified=int(grp.get("modified", date)),
                values=values,
            )
        )
    return groups


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def to_body_composition(group: MeasureGroup) -> BodyComposition | None:
    """Build a Garmin body composition payload, or None if there is no weight."""
    weight = group.values.get(MEAS_WEIGHT)
    if weight is None or weight <= 0:
        return None

    hydration_kg = group.values.get(MEAS_HYDRATION)
    percent_hydration = (
        hydration_kg / weight * 100.0 if hydration_kg is not None else None
    )

    return BodyComposition(
        timestamp=group.timestamp,
        weight=_round(weight),
        percent_fat=_round(group.values.get(MEAS_FAT_RATIO)),
        percent_hydration=_round(percent_hydration),
        bone_mass=_round(group.values.get(MEAS_BONE_MASS)),
        muscle_mass=_round(group.values.get(MEAS_MUSCLE_MASS)),
        visceral_fat_rating=_round(group.values.get(MEAS_VISCERAL_FAT)),
    )


def _in_range(value: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= value <= bounds[1]


def to_blood_pressure(group: MeasureGroup) -> BloodPressure | None:
    """Build a Garmin blood pressure payload, or None if the group has no valid BP."""
    systolic = group.values.get(MEAS_SYSTOLIC)
    diastolic = group.values.get(MEAS_DIASTOLIC)
    if systolic is None or diastolic is None:
        return None

    systolic_i = int(round(systolic))
    diastolic_i = int(round(diastolic))
    if not (
        _in_range(systolic_i, _SYSTOLIC_RANGE)
        and _in_range(diastolic_i, _DIASTOLIC_RANGE)
    ):
        return None

    pulse = group.values.get(MEAS_HEART_PULSE)
    pulse_i = int(round(pulse)) if pulse is not None else None
    if pulse_i is not None and not _in_range(pulse_i, _PULSE_RANGE):
        pulse_i = None

    return BloodPressure(
        timestamp=group.timestamp,
        systolic=systolic_i,
        diastolic=diastolic_i,
        pulse=pulse_i,
    )
