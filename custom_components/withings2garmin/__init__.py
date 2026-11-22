"""Withings to Garmin Sync integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from aiowithings import WithingsClient

from homeassistant.components.webhook import async_generate_id as webhook_generate_id
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_WEBHOOK_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .const import (
    ALL_FEATURES,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FEATURES,
    CONF_FEATURES,
    DEFAULT_FEATURES,
    DOMAIN,
    SERVICE_SYNC,
)
from .sync_engine import WithingsGarminSync
from .webhook import WithingsWebhookManager, cloud_component

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class WithingsGarminData:
    """Runtime data for a config entry."""

    engine: WithingsGarminSync
    webhook: WithingsWebhookManager


type WithingsGarminConfigEntry = ConfigEntry[WithingsGarminData]


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
            result = await entry.runtime_data.engine.async_sync(features)
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
    if CONF_WEBHOOK_ID not in entry.data:
        # Entries created before notifications were supported.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEBHOOK_ID: webhook_generate_id()}
        )

    implementation = await async_get_config_entry_implementation(hass, entry)
    oauth_session = OAuth2Session(hass, entry, implementation)
    refresh_lock = asyncio.Lock()

    async def _refresh_token() -> str:
        # Called by aiowithings before every request; refreshes (and persists the
        # rotated refresh token) only when the access token is about to expire.
        async with refresh_lock:
            await oauth_session.async_ensure_token_valid()
            return oauth_session.token[CONF_ACCESS_TOKEN]

    withings = WithingsClient(session=async_get_clientsession(hass))
    withings.refresh_token_function = _refresh_token

    engine = WithingsGarminSync(hass, entry, withings)
    manager = WithingsWebhookManager(hass, entry, withings, engine)
    entry.runtime_data = WithingsGarminData(engine=engine, webhook=manager)
    await manager.async_start()

    # Only reload when options change; data updates (token refreshes, last_sync,
    # webhook URL) also fire update listeners and must not cause a reload.
    loaded_options = dict(entry.options)

    async def _update_listener(hass: HomeAssistant, updated: ConfigEntry) -> None:
        if dict(updated.options) != loaded_options:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WithingsGarminConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.webhook.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the Nabu Casa cloudhook when the entry is removed."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    cloud = cloud_component()
    if webhook_id and cloud.async_active_subscription(hass):
        try:
            await cloud.async_delete_cloudhook(hass, webhook_id)
        except (cloud.CloudNotAvailable, ValueError):
            pass


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Entries before v3 used the built-in Withings OAuth handling; force re-add."""
    if entry.version < 3:
        _LOGGER.error(
            "This Withings to Garmin Sync entry was created by an older, incompatible "
            "version (Withings sign-in now uses Home Assistant Application "
            "Credentials). Please delete it and add the integration again"
        )
        return False
    return True
