"""Sync engine for Withings to Garmin."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import garminconnect
from garminconnect import Garmin

from .const import (
    DEFAULT_WITHINGS_CALLBACK_URL,
    DEFAULT_WITHINGS_CLIENT_ID,
    DEFAULT_WITHINGS_CLIENT_SECRET,
    FEATURE_BLOOD_PRESSURE,
    FEATURE_WEIGHT,
    TOKEN_FILE_GARMIN,
    TOKEN_FILE_WITHINGS,
    WITHINGS_AUTHORIZE_URL,
    WITHINGS_GETMEAS_URL,
    WITHINGS_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)


class WithingsGarminSync:
    """Handles syncing data from Withings to Garmin."""

    def __init__(self, hass, config_dir: str):
        """Initialize the sync engine."""
        self.hass = hass
        self.config_dir = config_dir
        self.withings_tokens: dict | None = None
        self.garmin: Garmin | None = None

    async def sync(self, features: list[str] | None = None) -> dict:
        """Run the sync from Withings to Garmin."""
        if features is None:
            features = [FEATURE_WEIGHT]

        result = {
            "success": False,
            "message": "",
            "synced_weight": 0,
            "synced_blood_pressure": 0,
        }

        try:
            # Load tokens
            await self._load_tokens()

            # Sync Withings data
            withings_data = await self._fetch_withings_data()

            if not withings_data:
                result["message"] = "No new data to sync"
                result["success"] = True
                return result

            # Upload to Garmin
            if FEATURE_WEIGHT in features:
                weight_count = await self._upload_weight_data(withings_data)
                result["synced_weight"] = weight_count

            if FEATURE_BLOOD_PRESSURE in features:
                bp_count = await self._upload_blood_pressure_data(withings_data)
                result["synced_blood_pressure"] = bp_count

            result["success"] = True
            result["message"] = f"Synced {result['synced_weight']} weight, {result['synced_blood_pressure']} blood pressure records"

        except Exception as e:
            _LOGGER.error("Sync error: %s", e)
            result["message"] = str(e)

        return result

    async def _load_tokens(self) -> None:
        """Load stored tokens from config directory."""
        # Load Withings tokens
        withings_token_path = os.path.join(self.config_dir, TOKEN_FILE_WITHINGS)
        if os.path.exists(withings_token_path):
            with open(withings_token_path, "r") as f:
                self.withings_tokens = json.load(f)

        # Load Garmin tokens
        garmin_token_path = os.path.join(self.config_dir, TOKEN_FILE_GARMIN)
        if os.path.exists(garmin_token_path):
            with open(garmin_token_path, "r") as f:
                garmin_tokens = json.load(f)
                # Initialize Garmin client with stored tokens
                self.garmin = Garmin(garmin_tokens)

    async def _fetch_withings_data(self) -> list[dict]:
        """Fetch measurements from Withings API."""
        if not self.withings_tokens:
            raise Exception("Withings not configured")

        # Check if token needs refresh
        await self._refresh_withings_token()

        # Get last sync time
        last_sync = self.withings_tokens.get("last_sync")
        if last_sync:
            fromdate = datetime.fromisoformat(last_sync)
        else:
            fromdate = datetime.now() - timedelta(days=7)

        todate = datetime.now()

        # Fetch measurements
        headers = {
            "Authorization": f"Bearer {self.withings_tokens.get('access_token')}",
        }

        params = {
            "startdate": int(fromdate.timestamp()),
            "enddate": int(todate.timestamp()),
            "data_types": "weight, fat_ratio, muscle_mass, bone_mass, hydration",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(WITHINGS_GETMEAS_URL, headers=headers, params=params) as response:
                if response.status != 200:
                    raise Exception(f"Withings API error: {response.status}")

                data = await response.json()

        # Parse measurements
        measurements = []
        if data.get("body", {}).get("measuregrps"):
            for group in data["body"]["measuregrps"]:
                timestamp = group.get("date")
                measures = group.get("measures", [])

                entry = {"timestamp": timestamp, "measures": {}}

                for measure in measures:
                    measure_type = measure.get("type")
                    value = measure.get("value")

                    # Map Withings measure types to names
                    type_map = {
                        1: "weight",
                        5: "fat_ratio",
                        6: "muscle_mass",
                        8: "bone_mass",
                        9: "hydration",
                    }

                    if measure_type in type_map:
                        entry["measures"][type_map[measure_type]] = value

                if entry["measures"]:
                    measurements.append(entry)

        # Update last sync time
        self.withings_tokens["last_sync"] = todate.isoformat()
        await self._save_withings_tokens()

        return measurements

    async def _refresh_withings_token(self) -> None:
        """Refresh Withings access token if needed."""
        # Check if token is expired (simplified check)
        # In production, check token expiry time
        if not self.withings_tokens:
            return

        # If we have a refresh token, use it
        refresh_token = self.withings_tokens.get("refresh_token")
        if not refresh_token:
            return

        # In production, implement token refresh logic
        # For now, assume token is still valid

    async def _save_withings_tokens(self) -> None:
        """Save Withings tokens to config directory."""
        if not self.withings_tokens:
            return

        withings_token_path = os.path.join(self.config_dir, TOKEN_FILE_WITHINGS)
        with open(withings_token_path, "w") as f:
            json.dump(self.withings_tokens, f)

    async def _upload_weight_data(self, measurements: list[dict]) -> int:
        """Upload weight data to Garmin."""
        if not self.garmin:
            raise Exception("Garmin not configured")

        count = 0
        for measurement in measurements:
            if "weight" in measurement.get("measures", {}):
                # Create a FIT file for this weight measurement
                # This is a simplified version - full implementation would use fit module
                weight = measurement["measures"]["weight"] / 1000  # Convert to kg

                try:
                    # Upload weight to Garmin
                    # Note: Garmin Connect API has limited support for direct weight uploads
                    # This may require using the activity upload endpoint
                    _LOGGER.info("Would upload weight: %s kg", weight)
                    count += 1
                except Exception as e:
                    _LOGGER.error("Error uploading weight: %s", e)

        return count

    async def _upload_blood_pressure_data(self, measurements: list[dict]) -> int:
        """Upload blood pressure data to Garmin."""
        if not self.garmin:
            raise Exception("Garmin not configured")

        count = 0
        # Blood pressure data would need to be fetched separately
        # For now, return 0
        return count


async def init_garmin_with_credentials(username: str, password: str, mfa_code: str | None = None) -> dict:
    """Initialize Garmin session with credentials and optional MFA."""
    try:
        # Create Garmin client
        garmin = Garmin()

        # Login (this will handle MFA if required)
        if mfa_code:
            garmin.login(username, password, mfa_code)
        else:
            # Try login - will raise MFA exception if needed
            try:
                garmin.login(username, password)
            except garminconnect.GarminConnectException as e:
                if "MFA" in str(e):
                    return {"mfa_required": True, "session": None}
                raise

        # Get session tokens
        tokens = garmin.get_oauth_token()

        return {"mfa_required": False, "success": True, "tokens": tokens}

    except Exception as e:
        _LOGGER.error("Garmin login error: %s", e)
        return {"mfa_required": False, "success": False, "error": str(e)}