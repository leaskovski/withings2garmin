"""Tests for the sync engine with a mocked aiowithings client and Garmin.

Requires Home Assistant and aiowithings to be installed
(`pip install homeassistant aiowithings garminconnect pytest`); skipped otherwise.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")
aiowithings = pytest.importorskip("aiowithings")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.withings2garmin import sync_engine  # noqa: E402
from custom_components.withings2garmin.const import CONF_LAST_SYNC  # noqa: E402

T = aiowithings.MeasurementType
REAL = aiowithings.MeasurementGroupCategory.REAL
OBJECTIVE = aiowithings.MeasurementGroupCategory.USER_OBJECTIVES


def _ts(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _group(group_id, taken, updated, values, category=REAL):
    return SimpleNamespace(
        group_id=group_id,
        taken_at=_ts(taken),
        updated_at=_ts(updated),
        category=category,
        measurements=[
            SimpleNamespace(measurement_type=t, value=v) for t, v in values.items()
        ],
    )


def _engine(groups=None, side_effect=None, last_sync=1700000000):
    entry = SimpleNamespace(
        data={"garmin_tokens": "tok", CONF_LAST_SYNC: last_sync},
        async_start_reauth=MagicMock(),
    )

    async def _executor(fn, *args):
        return fn(*args)

    def _update_entry(e, data):
        e.data = data

    hass = SimpleNamespace(
        async_add_executor_job=_executor,
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )
    withings = MagicMock()
    withings.get_measurement_since = AsyncMock(return_value=groups, side_effect=side_effect)
    return sync_engine.WithingsGarminSync(hass, entry, withings), entry, withings


def test_uploads_real_groups_and_advances_watermark():
    groups = [
        _group(2, 1710000500, 1710000600, {T.WEIGHT: 71.0}),
        _group(1, 1710000000, 1710000100, {T.WEIGHT: 72.0}),
        _group(3, 1710000700, 1710000800, {T.WEIGHT: 60.0}, category=OBJECTIVE),
    ]
    engine, entry, withings = _engine(groups)
    garmin_client = MagicMock()

    with (
        patch.object(sync_engine.garmin_api, "login_with_tokens", return_value=(garmin_client, "tok")),
        patch.object(sync_engine.garmin_api, "add_body_composition") as add_body,
    ):
        result = asyncio.run(engine.async_sync(["WEIGHT"]))

    assert result["success"], result
    assert result["synced_weight"] == 2  # objective group skipped
    # uploaded oldest-modified first
    assert [c.args[1].weight for c in add_body.call_args_list] == [72.0, 71.0]
    assert entry.data[CONF_LAST_SYNC] == 1710000600
    since = withings.get_measurement_since.call_args.args[0]
    assert since == _ts(1700000001)


def test_withings_auth_error_starts_reauth():
    engine, entry, _ = _engine(
        side_effect=aiowithings.WithingsAuthenticationFailedError("nope")
    )
    result = asyncio.run(engine.async_sync(["WEIGHT"]))
    assert not result["success"]
    entry.async_start_reauth.assert_called_once()


def test_withings_connection_error_does_not_start_reauth():
    engine, entry, _ = _engine(side_effect=aiowithings.WithingsConnectionError("down"))
    result = asyncio.run(engine.async_sync(["WEIGHT"]))
    assert not result["success"]
    entry.async_start_reauth.assert_not_called()
