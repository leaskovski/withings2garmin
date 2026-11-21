"""Sync engine for Withings to Garmin."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import ClientError, ClientResponseError
from aiowithings import (
    MeasurementGroup,
    MeasurementGroupCategory,
    MeasurementType,
    WithingsAuthenticationFailedError,
    WithingsClient,
    WithingsError,
    WithingsUnauthorizedError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util

from . import garmin as garmin_api
from .const import (
    CONF_GARMIN_TOKENS,
    CONF_LAST_SYNC,
    FEATURE_BLOOD_PRESSURE,
    FEATURE_WEIGHT,
    INITIAL_SYNC_DAYS,
)
from .measurements import (
    BLOOD_PRESSURE_TYPES,
    BODY_COMPOSITION_TYPES,
    height_at,
    heights_from_groups,
    to_blood_pressure,
    to_body_composition,
)

_LOGGER = logging.getLogger(__name__)


class SyncAuthError(Exception):
    """Credentials for one of the services are no longer valid."""


@contextmanager
def translate_withings_auth_errors() -> Iterator[None]:
    """Turn the ways an aiowithings call reports bad credentials into SyncAuthError.

    Token refresh happens inside aiowithings, so these can surface from any call.
    """
    try:
        yield
    except (WithingsAuthenticationFailedError, WithingsUnauthorizedError) as err:
        raise SyncAuthError(f"Withings rejected the access token: {err}") from err
    except ConfigEntryAuthFailed as err:
        # Newer Home Assistant: refresh token rejected (OAuth2TokenRequestReauthError)
        raise SyncAuthError(f"Withings token refresh failed: {err}") from err
    except ClientResponseError as err:
        # Older Home Assistant passes the token endpoint's error through as-is;
        # application_credentials maps a rejected refresh token to HTTP 400.
        if 400 <= err.status < 500 and err.status != 429:
            raise SyncAuthError(f"Withings token refresh failed: {err}") from err
        raise


class WithingsGarminSync:
    """Syncs measurements from Withings to Garmin for one config entry."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, withings: WithingsClient
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.withings = withings
        self._lock = asyncio.Lock()

    async def async_sync(self, features: list[str]) -> dict[str, Any]:
        """Run a sync. Concurrent calls are serialized."""
        async with self._lock:
            result: dict[str, Any] = {
                "success": False,
                "message": "",
                "synced_weight": 0,
                "synced_blood_pressure": 0,
            }
            try:
                await self._sync(features, result)
            except SyncAuthError as err:
                _LOGGER.error("Withings to Garmin sync needs re-authentication: %s", err)
                result["message"] = str(err)
                self.entry.async_start_reauth(self.hass)
            except (WithingsError, ClientError, garmin_api.GarminConnectConnectionError,
                    garmin_api.GarminConnectTooManyRequestsError) as err:
                _LOGGER.error("Withings to Garmin sync failed: %s", err)
                result["message"] = str(err)
            except Exception as err:  # noqa: BLE001 - never let a sync crash HA
                _LOGGER.exception("Unexpected error during Withings to Garmin sync")
                result["message"] = f"Unexpected error: {err}"
            return result

    async def _sync(self, features: list[str], result: dict[str, Any]) -> None:
        sync_weight = FEATURE_WEIGHT in features
        sync_bp = FEATURE_BLOOD_PRESSURE in features
        if not (sync_weight or sync_bp):
            result["success"] = True
            result["message"] = "No features selected"
            return

        meastypes: list[MeasurementType] = []
        if sync_weight:
            meastypes.extend(BODY_COMPOSITION_TYPES)
        if sync_bp:
            meastypes.extend(BLOOD_PRESSURE_TYPES)

        last_sync = self.entry.data.get(CONF_LAST_SYNC)
        if last_sync is None:
            since = dt_util.utcnow() - timedelta(days=INITIAL_SYNC_DAYS)
        else:
            # Withings returns groups modified at or after `lastupdate`; +1 avoids
            # re-uploading the last group we already handled.
            since = datetime.fromtimestamp(int(last_sync) + 1, tz=UTC)

        groups = sorted(
            (
                g
                for g in await self._fetch_groups(since, meastypes)
                # real measurements only, not user objectives
                if g.category == MeasurementGroupCategory.REAL
            ),
            key=lambda g: (g.updated_at, g.taken_at),
        )
        if not groups:
            result["success"] = True
            result["message"] = "No new data to sync"
            return

        # Height is not part of a weigh-in; Withings stores it as its own
        # (rarely updated) measurement, so fetch its full history for BMI.
        heights: list[tuple[datetime, float]] = []
        if sync_weight:
            heights = heights_from_groups(
                g
                for g in await self._fetch_groups(
                    datetime.fromtimestamp(0, tz=UTC), [MeasurementType.HEIGHT]
                )
                if g.category == MeasurementGroupCategory.REAL
            )
            if not heights:
                _LOGGER.warning("No height recorded in Withings; BMI will not be synced")

        client = await self._garmin_login()

        watermark: int | None = None
        failure: str | None = None
        try:
            for group in groups:
                try:
                    await self._upload_group(
                        client, group, sync_weight, sync_bp, heights, result
                    )
                except garmin_api.GarminConnectAuthenticationError as err:
                    raise SyncAuthError(f"Garmin rejected the session: {err}") from err
                except Exception as err:  # noqa: BLE001
                    # Stop here so the failed group (and everything after it) is
                    # retried next time instead of being silently skipped.
                    failure = f"Upload of measurement {group.group_id} failed: {err}"
                    _LOGGER.error(failure)
                    break
                watermark = int(group.updated_at.timestamp())
        finally:
            # Always record progress, even if we bail out with an auth error,
            # so already-uploaded measurements are not uploaded twice.
            if watermark is not None:
                await self._update_entry_data({CONF_LAST_SYNC: watermark})

        summary = (
            f"Synced {result['synced_weight']} weight and "
            f"{result['synced_blood_pressure']} blood pressure records"
        )
        if failure:
            result["message"] = f"{summary}; {failure}"
        else:
            result["success"] = True
            result["message"] = summary

    async def _upload_group(
        self,
        client: garmin_api.Garmin,
        group: MeasurementGroup,
        sync_weight: bool,
        sync_bp: bool,
        heights: list[tuple[datetime, float]],
        result: dict[str, Any],
    ) -> None:
        timestamp = dt_util.as_local(group.taken_at).isoformat()

        if sync_weight and (
            body := to_body_composition(group, height_at(heights, group.taken_at))
        ) is not None:
            await self.hass.async_add_executor_job(
                garmin_api.add_body_composition, client, body, timestamp
            )
            result["synced_weight"] += 1
            _LOGGER.info("Uploaded weigh-in at %s: %.2f kg", timestamp, body.weight)

        if sync_bp and (bp := to_blood_pressure(group)) is not None:
            await self.hass.async_add_executor_job(
                garmin_api.add_blood_pressure, client, bp, timestamp
            )
            result["synced_blood_pressure"] += 1
            _LOGGER.info(
                "Uploaded blood pressure at %s: %s/%s", timestamp, bp.systolic, bp.diastolic
            )

    async def _fetch_groups(
        self, since: datetime, meastypes: list[MeasurementType]
    ) -> list[MeasurementGroup]:
        """Fetch measure groups; token refresh happens inside aiowithings."""
        with translate_withings_auth_errors():
            return await self.withings.get_measurement_since(since, meastypes)

    async def _garmin_login(self) -> garmin_api.Garmin:
        """Log in to Garmin from stored tokens, persisting refreshed tokens."""
        stored = self.entry.data[CONF_GARMIN_TOKENS]
        try:
            client, tokens = await self.hass.async_add_executor_job(
                garmin_api.login_with_tokens, stored
            )
        except garmin_api.GarminConnectAuthenticationError as err:
            raise SyncAuthError(f"Garmin login failed: {err}") from err

        if tokens != stored:
            await self._update_entry_data({CONF_GARMIN_TOKENS: tokens})
        return client

    async def _update_entry_data(self, updates: dict[str, Any]) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, **updates}
        )
