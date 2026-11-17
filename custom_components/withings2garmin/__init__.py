"""Withings to Garmin Sync integration for Home Assistant."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    ALL_FEATURES,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FEATURES,
    CONF_CALLBACK_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FEATURES,
    CONF_SYNC_INTERVAL,
    DEFAULT_FEATURES,
    DEFAULT_SYNC_INTERVAL,
    DOMAIN,
    SERVICE_SYNC,
)
from .sync_engine import WithingsGarminSync
from .withings import WithingsClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WithingsGarminConfigEntry = ConfigEntry[WithingsGarminSync]


def _normalize_features(value: Any) -> list[str]:
    """Accept either a list or a comma separated string of features."""
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    features = [str(part).strip().upper() for part in value if str(part).strip()]
    invalid = [f for f in features if f not in ALL_FEATURES]
    if invalid:
        raise vol.Invalid(
            f"Unknown feature(s) {', '.join(invalid)}; valid: {', '.join(ALL_FEATURES)}"
        )
    return features


SERVICE_SYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_FEATURES): _normalize_features,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the sync service once for the whole integration."""

    async def async_handle_sync(call: ServiceCall) -> ServiceResponse:
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        entries: list[WithingsGarminConfigEntry] = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and (entry_id is None or entry.entry_id == entry_id)
        ]
        if not entries:
            raise HomeAssistantError(
                "No loaded Withings to Garmin Sync entry found"
                + (f" with id {entry_id}" if entry_id else "")
            )

        results: dict[str, Any] = {}
        failures: list[str] = []
        for entry in entries:
            features = call.data.get(ATTR_FEATURES) or entry.options.get(
                CONF_FEATURES, DEFAULT_FEATURES
            )
            result = await entry.runtime_data.async_sync(features)
            results[entry.entry_id] = result
            if not result["success"]:
                failures.append(f"{entry.title}: {result['message']}")

        if failures:
            raise HomeAssistantError("; ".join(failures))
        return {"results": results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC,
        async_handle_sync,
        schema=SERVICE_SYNC_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WithingsGarminConfigEntry) -> bool:
    """Set up Withings to Garmin Sync from a config entry."""
    withings = WithingsClient(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
        entry.data[CONF_CALLBACK_URL],
    )
    engine = WithingsGarminSync(hass, entry, withings)
    entry.runtime_data = engine

    interval = int(entry.options.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL))
    if interval > 0:

        async def _scheduled_sync(_now: datetime) -> None:
            features = entry.options.get(CONF_FEATURES, DEFAULT_FEATURES)
            result = await engine.async_sync(features)
            _LOGGER.debug("Scheduled sync result: %s", result)

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _scheduled_sync,
                timedelta(minutes=interval),
                name="withings2garmin scheduled sync",
                cancel_on_shutdown=True,
            )
        )

    # Only reload when options change; data updates (token refreshes, last_sync)
    # also fire update listeners and must not cause a reload.
    loaded_options = dict(entry.options)

    async def _update_listener(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if dict(updated.options) != loaded_options:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WithingsGarminConfigEntry) -> bool:
    """Unload a config entry."""
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 1 entries used an incompatible data layout and never worked; force re-add."""
    if entry.version < 2:
        _LOGGER.error(
            "This Withings to Garmin Sync entry was created by an older, incompatible "
            "version. Please delete it and add the integration again"
        )
        return False
    return True
