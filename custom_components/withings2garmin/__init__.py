"""Withings to Garmin Sync integration for Home Assistant."""

import json
import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, SERVICE_SYNC, TOKEN_FILE_GARMIN, TOKEN_FILE_WITHINGS
from .sync_engine import WithingsGarminSync

_LOGGER = logging.getLogger(__name__)

PLATFORMS = []  # No platforms - service only integration


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Withings to Garmin Sync from a config entry."""
    _LOGGER.info("Setting up Withings to Garmin Sync integration")

    # Create config directory for this entry
    config_dir = hass.config.path(f".withings2garmin_{entry.entry_id}")
    os.makedirs(config_dir, exist_ok=True)

    # Store config entry data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "config_dir": config_dir,
    }

    # Save Withings tokens if provided in config entry
    if entry.data.get("withings_tokens"):
        withings_token_path = os.path.join(config_dir, TOKEN_FILE_WITHINGS)
        with open(withings_token_path, "w") as f:
            json.dump(entry.data["withings_tokens"], f)
        _LOGGER.info("Saved Withings tokens to %s", withings_token_path)

    # Save Garmin tokens if provided in config entry
    if entry.data.get("garmin_tokens"):
        garmin_token_path = os.path.join(config_dir, TOKEN_FILE_GARMIN)
        with open(garmin_token_path, "w") as f:
            json.dump(entry.data["garmin_tokens"], f)
        _LOGGER.info("Saved Garmin tokens to %s", garmin_token_path)

    # Register services
    hass.services.async_register(
        DOMAIN, 
        SERVICE_SYNC, 
        async_sync_service,
        schema=cv.make_entity_service_schema(
            {
                "features": cv.string,
            }
        )
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Withings to Garmin Sync integration")

    # Remove services
    hass.services.async_remove(DOMAIN, SERVICE_SYNC)

    # Clean up data
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        del hass.data[DOMAIN][entry.entry_id]

    return True


async def async_sync_service(call: ServiceCall) -> dict:
    """Handle sync service call."""
    features = call.data.get("features")

    # Get the config entry
    entries = call.hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {"success": False, "message": "Integration not configured"}

    entry = entries[0]
    config_dir = call.hass.data[DOMAIN][entry.entry_id]["config_dir"]

    # Create sync engine
    sync_engine = WithingsGarminSync(call.hass, config_dir)

    # Run sync
    result = await sync_engine.sync(features)

    return result


# Service schema
SERVICE_SYNC_SCHEMA = cv.make_entity_service_schema(
    {
        "features": cv.string,
    }
)