"""Config flow for Withings to Garmin Sync."""

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DEFAULT_WITHINGS_CALLBACK_URL,
    DEFAULT_WITHINGS_CLIENT_ID,
    DEFAULT_WITHINGS_CLIENT_SECRET,
    DOMAIN,
    WITHINGS_AUTHORIZE_URL,
    WITHINGS_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

STEP_WITHINGS_CREDS_SCHEMA = vol.Schema(
    {
        vol.Optional("client_id", default=DEFAULT_WITHINGS_CLIENT_ID): str,
        vol.Optional("client_secret", default=DEFAULT_WITHINGS_CLIENT_SECRET): str,
    }
)

STEP_WITHINGS_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)

STEP_GARMIN_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_MFA_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("mfa_code"): str,
    }
)


class WithingsGarminConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Withings to Garmin Sync."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.withings_client_id: str = DEFAULT_WITHINGS_CLIENT_ID
        self.withings_client_secret: str = DEFAULT_WITHINGS_CLIENT_SECRET
        self.garmin_username: str | None = None
        self.garmin_password: str | None = None
        self.garmin_mfa_required: bool = False
        self.garmin_tokens: dict | None = None
        self.withings_tokens: dict = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Initial step - show menu to choose which account to configure."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["configure_withings", "configure_garmin"],
            description_placeholders={"name": "Withings to Garmin Sync"},
        )

    async def async_step_configure_withings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure Withings OAuth credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.withings_client_id = user_input.get("client_id", DEFAULT_WITHINGS_CLIENT_ID)
            self.withings_client_secret = user_input.get("client_secret", DEFAULT_WITHINGS_CLIENT_SECRET)

            # Generate authorization URL and show to user
            auth_url = self._build_auth_url()
            
            return self.async_show_form(
                step_id="withings_authorize",
                errors=errors,
                description_placeholders={"auth_url": auth_url},
            )

        return self.async_show_form(
            step_id="configure_withings",
            data_schema=STEP_WITHINGS_CREDS_SCHEMA,
            errors=errors,
        )

    def _build_auth_url(self) -> str:
        """Build the Withings authorization URL."""
        # Generate a unique state parameter
        state = f"ha_withings_{self.flow_id}"
        
        auth_url = (
            f"{WITHINGS_AUTHORIZE_URL}?"
            f"response_type=code&"
            f"client_id={self.withings_client_id}&"
            f"redirect_uri={DEFAULT_WITHINGS_CALLBACK_URL}&"
            f"scope=user.metrics&"
            f"state={state}"
        )
        return auth_url

    async def async_step_withings_authorize(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show authorization URL and collect code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input.get("code", "").strip()
            
            if not code:
                errors["base"] = "no_code"
            else:
                try:
                    # Exchange code for tokens
                    token_data = await self._exchange_withings_code(code)
                    
                    if token_data:
                        self.withings_tokens = token_data
                        # Move to Garmin configuration
                        return await self.async_step_configure_garmin()
                    
                except Exception as e:
                    _LOGGER.error("Withings token exchange error: %s", e)
                    errors["base"] = "token_exchange_error"

        # Show the authorization URL again
        auth_url = self._build_auth_url()
        
        return self.async_show_form(
            step_id="withings_authorize",
            data_schema=STEP_WITHINGS_CODE_SCHEMA,
            errors=errors,
            description_placeholders={
                "auth_url": auth_url,
                "callback_url": DEFAULT_WITHINGS_CALLBACK_URL,
            },
        )

    async def _exchange_withings_code(self, code: str) -> dict | None:
        """Exchange authorization code for access token."""
        try:
            async with aiohttp.ClientSession() as session:
                data = {
                    "grant_type": "authorization_code",
                    "client_id": self.withings_client_id,
                    "client_secret": self.withings_client_secret,
                    "code": code,
                    "redirect_uri": DEFAULT_WITHINGS_CALLBACK_URL,
                }

                async with session.post(WITHINGS_TOKEN_URL, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("body"):
                            return {
                                "access_token": result["body"].get("access_token"),
                                "refresh_token": result["body"].get("refresh_token"),
                                "userid": result["body"].get("userid"),
                                "expires_in": result["body"].get("expires_in"),
                            }
                    else:
                        error_text = await response.text()
                        _LOGGER.error("Withings token exchange failed: %s", error_text)
                        raise Exception(f"Token exchange failed: {response.status}")

        except Exception as e:
            _LOGGER.error("Error exchanging Withings code: %s", e)
            raise

        return None

    async def async_step_configure_garmin(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Configure Garmin credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self.garmin_username = user_input.get(CONF_USERNAME)
            self.garmin_password = user_input.get(CONF_PASSWORD)

            try:
                # Attempt to login to Garmin
                result = await self._test_garmin_login(
                    self.garmin_username,
                    self.garmin_password,
                )

                if result.get("mfa_required"):
                    self.garmin_mfa_required = True
                    return await self.async_step_garmin_mfa()

                if result.get("success"):
                    # Store Garmin tokens
                    self.garmin_tokens = result.get("tokens")
                    return self.async_create_entry(
                        title="Withings to Garmin Sync",
                        data={
                            "withings_configured": True,
                            "garmin_configured": True,
                            "withings_tokens": self.withings_tokens,
                            "garmin_username": self.garmin_username,
                            "garmin_tokens": self.garmin_tokens,
                        },
                    )

            except Exception as e:
                _LOGGER.error("Garmin login error: %s", e)
                errors["base"] = "garmin_login_error"

        return self.async_show_form(
            step_id="configure_garmin",
            data_schema=STEP_GARMIN_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_garmin_mfa(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle Garmin MFA step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            mfa_code = user_input.get("mfa_code")

            try:
                result = await self._verify_garmin_mfa(
                    self.garmin_username,
                    self.garmin_password,
                    mfa_code,
                )

                if result.get("success"):
                    # Store Garmin tokens
                    self.garmin_tokens = result.get("tokens")
                    return self.async_create_entry(
                        title="Withings to Garmin Sync",
                        data={
                            "withings_configured": True,
                            "garmin_configured": True,
                            "withings_tokens": self.withings_tokens,
                            "garmin_username": self.garmin_username,
                            "garmin_tokens": self.garmin_tokens,
                        },
                    )

            except Exception as e:
                _LOGGER.error("Garmin MFA error: %s", e)
                errors["base"] = "garmin_mfa_error"

        return self.async_show_form(
            step_id="garmin_mfa",
            data_schema=STEP_MFA_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"username": self.garmin_username or ""},
        )

    async def _test_garmin_login(self, username: str, password: str) -> dict:
        """Test Garmin login and check if MFA is required."""
        try:
            from garminconnect import Garmin
            
            garmin = Garmin()
            try:
                garmin.login(username, password)
                # Get OAuth tokens after successful login
                tokens = garmin.get_oauth_token()
                self.garmin_tokens = tokens
                return {"mfa_required": False, "success": True, "tokens": tokens}
            except Exception as e:
                error_msg = str(e).lower()
                if "mfa" in error_msg or "multi-factor" in error_msg or "2fa" in error_msg:
                    return {"mfa_required": True, "success": False}
                # Other errors - re-raise
                raise

        except ImportError:
            _LOGGER.warning("garminconnect not installed")
            return {"mfa_required": True, "success": False, "error": "Library not installed"}
        except Exception as e:
            _LOGGER.error("Garmin login error: %s", e)
            return {"mfa_required": False, "success": False, "error": str(e)}

    async def _verify_garmin_mfa(self, username: str, password: str, mfa_code: str) -> dict:
        """Verify Garmin MFA code."""
        try:
            from garminconnect import Garmin
            
            garmin = Garmin()
            garmin.login(username, password, mfa_code)
            
            # Get OAuth tokens after successful MFA login
            tokens = garmin.get_oauth_token()
            self.garmin_tokens = tokens
            
            return {"success": True, "tokens": tokens}

        except Exception as e:
            _LOGGER.error("Garmin MFA verification failed: %s", e)
            return {"success": False, "error": str(e)}