import asyncio
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sync_engine import WithingsGarminSync


def test_upload_weight_data_uses_withings_timestamp_and_values():
    sync = WithingsGarminSync(None, "/tmp")
    sync.garmin = MagicMock()

    measurement = {
        "timestamp": 1710000000,
        "measures": {
            "weight": 72000,
            "fat_ratio": 2500,
            "muscle_mass": 50000,
            "bone_mass": 3000,
            "hydration": 5600,
        },
    }

    result = asyncio.run(sync._upload_weight_data([measurement]))

    assert result == 1
    sync.garmin.add_body_composition.assert_called_once()

    args, _ = sync.garmin.add_body_composition.call_args
    timestamp = args[0]
    weight = args[1]
    percent_fat = args[2]
    percent_hydration = args[3]
    bone_mass = args[5]
    muscle_mass = args[6]

    assert timestamp.endswith("+00:00") or timestamp.endswith("Z")
    assert abs(weight - 72.0) < 0.0001
    assert abs(percent_fat - 25.0) < 0.0001
    assert abs(percent_hydration - 56.0) < 0.0001
    assert abs(bone_mass - 3.0) < 0.0001
    assert abs(muscle_mass - 50.0) < 0.0001
