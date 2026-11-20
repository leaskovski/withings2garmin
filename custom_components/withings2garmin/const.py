"""Constants for Withings to Garmin Sync integration."""

from typing import Final

DOMAIN: Final = "withings2garmin"
NAME: Final = "Withings to Garmin Sync"

# Withings OAuth scope (endpoints and client come from aiowithings)
WITHINGS_SCOPE: Final = "user.metrics"

# Config entry data keys. The Withings token is stored under "token" and
# "auth_implementation" by Home Assistant's OAuth2 flow.
CONF_GARMIN_EMAIL: Final = "garmin_email"
CONF_GARMIN_TOKENS: Final = "garmin_tokens"
CONF_LAST_SYNC: Final = "last_sync"

# Options
CONF_SYNC_INTERVAL: Final = "sync_interval"
CONF_FEATURES: Final = "features"
DEFAULT_SYNC_INTERVAL: Final = 60  # minutes, 0 disables scheduled sync

# Service
SERVICE_SYNC: Final = "sync"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_FEATURES: Final = "features"

# Features
FEATURE_WEIGHT: Final = "WEIGHT"
FEATURE_BLOOD_PRESSURE: Final = "BLOOD_PRESSURE"
ALL_FEATURES: Final = [FEATURE_WEIGHT, FEATURE_BLOOD_PRESSURE]
DEFAULT_FEATURES: Final = [FEATURE_WEIGHT]

# How far back to look on the very first sync
INITIAL_SYNC_DAYS: Final = 7
