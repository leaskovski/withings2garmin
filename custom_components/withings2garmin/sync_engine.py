"""Sync engine for Withings to Garmin."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import garmin as garmin_api
from .const import (
    CONF_GARMIN_TOKENS,
    CONF_LAST_SYNC,
    CONF_WITHINGS_TOKEN,
    FEATURE_BLOOD_PRESSURE,
    FEATURE_WEIGHT,
    INITIAL_SYNC_DAYS,
)
from .measurements import (
    BLOOD_PRESSURE_TYPES,
    BODY_COMPOSITION_TYPES,
    MeasureGroup,
    parse_groups,
    to_blood_pressure,
    to_body_composition,
)
from .withings import WithingsAuthError, WithingsClient, WithingsError

_LOGGER = logging.getLogger(__name__)

# Refresh the Withings access token when it has less than this left (seconds)
_TOKEN_REFRESH_MARGIN = 300


class SyncAuthError(Exception):
    """Credentials for one of the services are no longer valid."""


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
            except (WithingsError, garmin_api.GarminConnectConnectionError,
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

        meastypes: list[int] = []
        if sync_weight:
            meastypes.extend(BODY_COMPOSITION_TYPES)
        if sync_bp:
            meastypes.extend(BLOOD_PRESSURE_TYPES)

        access_token = await self._ensure_withings_token()

        last_sync = self.entry.data.get(CONF_LAST_SYNC)
        if last_sync is None:
            lastupdate = int(
                (dt_util.utcnow() - timedelta(days=INITIAL_SYNC_DAYS)).timestamp()
            )
        else:
            # Withings returns groups modified at or after `lastupdate`; +1 avoids
            # re-uploading the last group we already handled.
            lastupdate = int(last_sync) + 1

        try:
            raw_groups = await self.withings.get_measure_groups(
                access_token, lastupdate, meastypes
            )
        except WithingsAuthError as err:
            raise SyncAuthError(f"Withings rejected the access token: {err}") from err

        groups = sorted(parse_groups(raw_groups), key=lambda g: (g.modified, g.date))
        if not groups:
            result["success"] = True
            result["message"] = "No new data to sync"
            return

        client = await self._garmin_login()

        watermark: int | None = None
        failure: str | None = None
        try:
            for group in groups:
                try:
                    await self._upload_group(client, group, sync_weight, sync_bp, result)
                except garmin_api.GarminConnectAuthenticationError as err:
                    raise SyncAuthError(f"Garmin rejected the session: {err}") from err
                except Exception as err:  # noqa: BLE001
                    # Stop here so the failed group (and everything after it) is
                    # retried next time instead of being silently skipped.
                    failure = f"Upload of measurement {group.grpid} failed: {err}"
                    _LOGGER.error(failure)
                    break
                watermark = group.modified
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
        group: MeasureGroup,
        sync_weight: bool,
        sync_bp: bool,
        result: dict[str, Any],
    ) -> None:
        timestamp = dt_util.as_local(group.timestamp).isoformat()

        if sync_weight and (body := to_body_composition(group)) is not None:
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

    async def _ensure_withings_token(self) -> str:
        """Return a valid Withings access token, refreshing and persisting it if needed."""
        token: dict[str, Any] = dict(self.entry.data[CONF_WITHINGS_TOKEN])
        if token.get("expires_at", 0) - _TOKEN_REFRESH_MARGIN > time.time():
            return token["access_token"]

        _LOGGER.debug("Refreshing Withings access token")
        try:
            new_token = await self.withings.refresh_token(token["refresh_token"])
        except WithingsAuthError as err:
            raise SyncAuthError(f"Withings token refresh failed: {err}") from err

        # Withings rotates refresh tokens, so the new pair must be saved immediately
        await self._update_entry_data({CONF_WITHINGS_TOKEN: new_token})
        return new_token["access_token"]

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
