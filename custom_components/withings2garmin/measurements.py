"""Pure helpers that turn aiowithings measurement groups into Garmin payloads.

This module deliberately has no Home Assistant or network dependencies so it
can be unit tested in isolation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from aiowithings import MeasurementGroup, MeasurementType

BODY_COMPOSITION_TYPES = (
    MeasurementType.WEIGHT,
    MeasurementType.FAT_RATIO,
    MeasurementType.MUSCLE_MASS,
    MeasurementType.HYDRATION,  # kg (mass of body water, not a percentage)
    MeasurementType.BONE_MASS,
    MeasurementType.VISCERAL_FAT,
)
BLOOD_PRESSURE_TYPES = (
    MeasurementType.DIASTOLIC_BLOOD_PRESSURE,
    MeasurementType.SYSTOLIC_BLOOD_PRESSURE,
    MeasurementType.HEART_RATE,
)

# Ranges accepted by garminconnect's set_blood_pressure()
_SYSTOLIC_RANGE = (70, 260)
_DIASTOLIC_RANGE = (40, 150)
_PULSE_RANGE = (20, 250)

# Plausible human height in metres; anything else is treated as bad data
_HEIGHT_RANGE_M = (0.5, 2.75)


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
    bmi: float | None = None


@dataclass(frozen=True)
class BloodPressure:
    """Payload for Garmin set_blood_pressure()."""

    timestamp: datetime
    systolic: int
    diastolic: int
    pulse: int | None = None


def _values(group: MeasurementGroup) -> dict[MeasurementType, float]:
    """Map measurement type to (already unit-decoded) value."""
    return {m.measurement_type: m.value for m in group.measurements}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _plausible_height(value: float | None) -> bool:
    return value is not None and _HEIGHT_RANGE_M[0] <= value <= _HEIGHT_RANGE_M[1]


def heights_from_groups(
    groups: Iterable[MeasurementGroup],
) -> list[tuple[datetime, float]]:
    """Extract plausible height readings (metres), oldest first."""
    heights = [
        (group.taken_at, value)
        for group in groups
        if _plausible_height(value := _values(group).get(MeasurementType.HEIGHT))
    ]
    return sorted(heights, key=lambda h: h[0])


def height_at(heights: list[tuple[datetime, float]], when: datetime) -> float | None:
    """Height in effect at `when`: the latest reading taken at or before it.

    Falls back to the earliest reading when every reading is newer, since a
    weigh-in made before the height was entered in Withings still needs a BMI.
    """
    if not heights:
        return None
    current = heights[0][1]
    for taken_at, value in heights:
        if taken_at > when:
            break
        current = value
    return current


def calculate_bmi(weight_kg: float, height_m: float | None) -> float | None:
    """BMI = weight (kg) / height (m) squared, or None without a usable height."""
    if height_m is None or height_m <= 0 or weight_kg <= 0:
        return None
    return round(weight_kg / (height_m**2), 1)


def to_body_composition(
    group: MeasurementGroup, height_m: float | None = None
) -> BodyComposition | None:
    """Build a Garmin body composition payload, or None if there is no weight.

    BMI is calculated from the weight and `height_m`; a height measured in the
    same group takes precedence.
    """
    values = _values(group)
    weight = values.get(MeasurementType.WEIGHT)
    if weight is None or weight <= 0:
        return None

    group_height = values.get(MeasurementType.HEIGHT)
    if _plausible_height(group_height):
        height_m = group_height

    hydration_kg = values.get(MeasurementType.HYDRATION)
    percent_hydration = (
        hydration_kg / weight * 100.0 if hydration_kg is not None else None
    )

    return BodyComposition(
        timestamp=group.taken_at,
        weight=_round(weight),
        percent_fat=_round(values.get(MeasurementType.FAT_RATIO)),
        percent_hydration=_round(percent_hydration),
        bone_mass=_round(values.get(MeasurementType.BONE_MASS)),
        muscle_mass=_round(values.get(MeasurementType.MUSCLE_MASS)),
        visceral_fat_rating=_round(values.get(MeasurementType.VISCERAL_FAT)),
        bmi=calculate_bmi(weight, height_m),
    )


def _in_range(value: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= value <= bounds[1]


def to_blood_pressure(group: MeasurementGroup) -> BloodPressure | None:
    """Build a Garmin blood pressure payload, or None if the group has no valid BP."""
    values = _values(group)
    systolic = values.get(MeasurementType.SYSTOLIC_BLOOD_PRESSURE)
    diastolic = values.get(MeasurementType.DIASTOLIC_BLOOD_PRESSURE)
    if systolic is None or diastolic is None:
        return None

    systolic_i = int(round(systolic))
    diastolic_i = int(round(diastolic))
    if not (
        _in_range(systolic_i, _SYSTOLIC_RANGE)
        and _in_range(diastolic_i, _DIASTOLIC_RANGE)
    ):
        return None

    pulse = values.get(MeasurementType.HEART_RATE)
    pulse_i = int(round(pulse)) if pulse is not None else None
    if pulse_i is not None and not _in_range(pulse_i, _PULSE_RANGE):
        pulse_i = None

    return BloodPressure(
        timestamp=group.taken_at,
        systolic=systolic_i,
        diastolic=diastolic_i,
        pulse=pulse_i,
    )
