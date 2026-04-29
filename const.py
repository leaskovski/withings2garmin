"""Constants for Withings to Garmin Sync integration."""

DOMAIN = "withings2garmin"
NAME = "Withings to Garmin Sync"

# Withings OAuth
WITHINGS_AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
WITHINGS_TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
WITHINGS_GETMEAS_URL = "https://wbsapi.withings.net/measure?action=getmeas"

# Default Withings app credentials (from withings-sync)
DEFAULT_WITHINGS_CLIENT_ID = "xxx"
DEFAULT_WITHINGS_CLIENT_SECRET = "yyy"
DEFAULT_WITHINGS_CALLBACK_URL = "zzz"

# Token storage
TOKEN_FILE_WITHINGS = "withings_user.json"
TOKEN_FILE_GARMIN = "garmin_session.json"

# Service
SERVICE_SYNC = "sync"

# Features
FEATURE_WEIGHT = "WEIGHT"
FEATURE_BLOOD_PRESSURE = "BLOOD_PRESSURE"
DEFAULT_FEATURES = [FEATURE_WEIGHT]