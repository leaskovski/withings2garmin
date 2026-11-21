"""Pure helpers that turn aiowithings measurement groups into Garmin payloads.

This module deliberately has no Home Assistant or network dependencies so it
can be unit tested in isolation.
"""

from __future__ import annotations

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


def _values(group: MeasurementGroup) -> dict[MeasurementType, float]:
    """Map measurement type to (already unit-decoded) value."""
    return {m.measurement_type: m.value for m in group.measurements}


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def to_body_composition(group: MeasurementGroup) -> BodyComposition | None:
    """Build a Garmin body composition payload, or None if there is no weight."""
    values = _values(group)
    weight = values.get(MeasurementType.WEIGHT)
    if weight is None or weight <= 0:
        return None

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
