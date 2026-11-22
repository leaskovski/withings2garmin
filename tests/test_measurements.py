"""Tests for the Withings -> Garmin conversion helpers.

measurements.py has no Home Assistant dependencies (only aiowithings), so it is
loaded directly from its file path; run with `pytest tests/`.

Groups are built as simple namespaces with the same attributes as
aiowithings.MeasurementGroup, whose constructor differs between 3.x and 4.x.
Unit decoding (value * 10^unit) is done by aiowithings and not re-tested here.
"""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

aiowithings = pytest.importorskip("aiowithings")
T = aiowithings.MeasurementType

_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "withings2garmin"
    / "measurements.py"
)
_spec = importlib.util.spec_from_file_location("w2g_measurements", _PATH)
m = importlib.util.module_from_spec(_spec)
# @dataclass resolves string annotations via sys.modules, so register first
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)

TAKEN_AT = datetime.fromtimestamp(1710000000, tz=timezone.utc)


def _grp(values):
    return SimpleNamespace(
        taken_at=TAKEN_AT,
        measurements=[
            SimpleNamespace(measurement_type=t, value=v) for t, v in values.items()
        ],
    )


def test_body_composition():
    body = m.to_body_composition(
        _grp(
            {
                T.WEIGHT: 72.35,
                T.FAT_RATIO: 25.5,
                T.MUSCLE_MASS: 50.12,
                T.HYDRATION: 39.80,  # kg of water
                T.BONE_MASS: 2.85,
                T.VISCERAL_FAT: 8,
            }
        )
    )

    assert body is not None
    assert body.timestamp == TAKEN_AT
    assert body.weight == 72.35
    assert body.percent_fat == 25.5
    assert body.muscle_mass == 50.12
    assert body.bone_mass == 2.85
    assert body.percent_hydration == round(39.80 / 72.35 * 100, 2)
    assert body.visceral_fat_rating == 8


def test_body_composition_requires_weight():
    assert m.to_body_composition(_grp({T.FAT_RATIO: 25.5})) is None


def test_weight_only_group():
    body = m.to_body_composition(_grp({T.WEIGHT: 80}))
    assert body.weight == 80
    assert body.percent_fat is None
    assert body.percent_hydration is None


def test_blood_pressure():
    group = _grp(
        {
            T.SYSTOLIC_BLOOD_PRESSURE: 121,
            T.DIASTOLIC_BLOOD_PRESSURE: 79,
            T.HEART_RATE: 64,
        }
    )
    bp = m.to_blood_pressure(group)
    assert (bp.systolic, bp.diastolic, bp.pulse) == (121, 79, 64)
    # A BP-only group must not be treated as a weigh-in
    assert m.to_body_composition(group) is None


def test_blood_pressure_out_of_range_is_skipped():
    group = _grp({T.SYSTOLIC_BLOOD_PRESSURE: 300, T.DIASTOLIC_BLOOD_PRESSURE: 79})
    assert m.to_blood_pressure(group) is None


def test_invalid_pulse_is_dropped_not_fatal():
    group = _grp(
        {
            T.SYSTOLIC_BLOOD_PRESSURE: 120,
            T.DIASTOLIC_BLOOD_PRESSURE: 80,
            T.HEART_RATE: 5,
        }
    )
    assert m.to_blood_pressure(group).pulse is None


def test_unknown_measurement_types_are_ignored():
    body = m.to_body_composition(_grp({T.WEIGHT: 70, T.UNKNOWN: 1}))
    assert body.weight == 70


def test_bmi_from_height():
    body = m.to_body_composition(_grp({T.WEIGHT: 72.35}), height_m=1.78)
    assert body.bmi == round(72.35 / 1.78**2, 1)  # 22.8


def test_bmi_none_without_height():
    assert m.to_body_composition(_grp({T.WEIGHT: 72.35})).bmi is None


def test_height_in_same_group_takes_precedence():
    body = m.to_body_composition(_grp({T.WEIGHT: 80, T.HEIGHT: 2.0}), height_m=1.5)
    assert body.bmi == 20.0


def test_height_at_picks_reading_in_effect():
    t = lambda s: datetime.fromtimestamp(s, tz=timezone.utc)
    heights = m.heights_from_groups(
        [
            SimpleNamespace(taken_at=t(300), measurements=[SimpleNamespace(measurement_type=T.HEIGHT, value=1.9)]),
            SimpleNamespace(taken_at=t(100), measurements=[SimpleNamespace(measurement_type=T.HEIGHT, value=1.8)]),
            SimpleNamespace(taken_at=t(200), measurements=[SimpleNamespace(measurement_type=T.HEIGHT, value=18.0)]),  # implausible
        ]
    )
    assert [h for _, h in heights] == [1.8, 1.9]
    assert m.height_at(heights, t(50)) == 1.8  # before first reading: earliest
    assert m.height_at(heights, t(250)) == 1.8
    assert m.height_at(heights, t(300)) == 1.9
    assert m.height_at([], t(300)) is None
