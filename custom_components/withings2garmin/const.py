"""Constants for Withings to Garmin Sync integration."""

from datetime import timedelta
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
# The callback URL last subscribed with Withings, so it can be revoked if the
# URL changes (e.g. switching between Nabu Casa and an external URL). The
# webhook id itself is stored under Home Assistant's CONF_WEBHOOK_ID.
CONF_WEBHOOK_URL: Final = "webhook_url"

# Options
CONF_FEATURES: Final = "features"

# Withings notifications
# If no notification arrives for this long, run a fallback sync and check the
# subscription is still registered with Withings.
WEBHOOK_WATCHDOG: Final = timedelta(hours=24)
# Notifications that arrive close together (e.g. weight + blood pressure from
# one measurement) are coalesced into a single sync.
WEBHOOK_SYNC_DELAY: Final = 10  # seconds
# Withings probes the callback URL on every subscribe call and fails if they
# arrive too quickly.
WEBHOOK_SUBSCRIBE_DELAY: Final = 5  # seconds
# Retry delay when subscribing fails for a transient reason.
WEBHOOK_RETRY_DELAY: Final = timedelta(minutes=30)

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
