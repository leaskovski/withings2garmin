"""Tests for the Withings -> Garmin conversion helpers.

measurements.py has no Home Assistant dependencies, so it is loaded directly
from its file path; run with `pytest tests/`.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "withings2garmin"
    / "measurements.py"
)
_spec = importlib.util.spec_from_file_location("w2g_measurements", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _grp(measures, date=1710000000, modified=1710000100, grpid=1):
    return {
        "grpid": grpid,
        "date": date,
        "modified": modified,
        "category": 1,
        "measures": [
            {"type": t, "value": v, "unit": u} for t, (v, u) in measures.items()
        ],
    }


def test_decode_value_applies_unit_exponent():
    assert m.decode_value({"value": 72350, "unit": -3}) == 72.35
    assert m.decode_value({"value": 255, "unit": -1}) == 25.5
    assert m.decode_value({"value": 120, "unit": 0}) == 120


def test_body_composition_from_real_withings_shape():
    group = m.parse_groups(
        [
            _grp(
                {
                    m.MEAS_WEIGHT: (72350, -3),  # 72.35 kg
                    m.MEAS_FAT_RATIO: (255, -1),  # 25.5 %
                    m.MEAS_MUSCLE_MASS: (5012, -2),  # 50.12 kg
                    m.MEAS_HYDRATION: (3980, -2),  # 39.80 kg of water
                    m.MEAS_BONE_MASS: (285, -2),  # 2.85 kg
                    m.MEAS_VISCERAL_FAT: (8, 0),
                }
            )
        ]
    )[0]

    body = m.to_body_composition(group)

    assert body is not None
    assert body.timestamp == datetime.fromtimestamp(1710000000, tz=timezone.utc)
    assert body.weight == 72.35
    assert body.percent_fat == 25.5
    assert body.muscle_mass == 50.12
    assert body.bone_mass == 2.85
    assert body.percent_hydration == round(39.80 / 72.35 * 100, 2)
    assert body.visceral_fat_rating == 8


def test_body_composition_requires_weight():
    group = m.parse_groups([_grp({m.MEAS_FAT_RATIO: (255, -1)})])[0]
    assert m.to_body_composition(group) is None


def test_weight_only_group():
    group = m.parse_groups([_grp({m.MEAS_WEIGHT: (80, 0)})])[0]
    body = m.to_body_composition(group)
    assert body.weight == 80
    assert body.percent_fat is None
    assert body.percent_hydration is None


def test_blood_pressure():
    group = m.parse_groups(
        [
            _grp(
                {
                    m.MEAS_SYSTOLIC: (121, 0),
                    m.MEAS_DIASTOLIC: (79, 0),
                    m.MEAS_HEART_PULSE: (64, 0),
                }
            )
        ]
    )[0]
    bp = m.to_blood_pressure(group)
    assert (bp.systolic, bp.diastolic, bp.pulse) == (121, 79, 64)
    # A BP-only group must not be treated as a weigh-in
    assert m.to_body_composition(group) is None


def test_blood_pressure_out_of_range_is_skipped():
    group = m.parse_groups(
        [_grp({m.MEAS_SYSTOLIC: (300, 0), m.MEAS_DIASTOLIC: (79, 0)})]
    )[0]
    assert m.to_blood_pressure(group) is None


def test_invalid_pulse_is_dropped_not_fatal():
    group = m.parse_groups(
        [
            _grp(
                {
                    m.MEAS_SYSTOLIC: (120, 0),
                    m.MEAS_DIASTOLIC: (80, 0),
                    m.MEAS_HEART_PULSE: (5, 0),
                }
            )
        ]
    )[0]
    assert m.to_blood_pressure(group).pulse is None


def test_parse_groups_ignores_malformed_measures_and_defaults_modified():
    groups = m.parse_groups(
        [{"grpid": 9, "date": 100, "measures": [{"type": 1}, {"type": 1, "value": 70, "unit": 0}]}]
    )
    assert groups[0].values == {1: 70.0}
    assert groups[0].modified == 100
