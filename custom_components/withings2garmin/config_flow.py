"""Config flow for Withings to Garmin Sync."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import garmin as garmin_api
from .const import (
    ALL_FEATURES,
    CONF_CALLBACK_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_FEATURES,
    CONF_GARMIN_EMAIL,
    CONF_GARMIN_TOKENS,
    CONF_SYNC_INTERVAL,
    CONF_WITHINGS_TOKEN,
    DEFAULT_FEATURES,
    DEFAULT_SYNC_INTERVAL,
    DOMAIN,
    NAME,
)
from .withings import WithingsAuthError, WithingsClient, WithingsError

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"
CONF_MFA_CODE = "mfa_code"


def _extract_code(value: str, expected_state: str) -> tuple[str | None, str | None]:
    """Accept either a bare code or the full redirect URL. Returns (code, error)."""
    value = value.strip()
    if "code=" not in value:
        return (value or None), (None if value else "no_code")

    query = parse_qs(urlparse(value).query) if "://" in value else parse_qs(value.lstrip("?"))
    code = (query.get("code") or [None])[0]
    state = (query.get("state") or [None])[0]
    if not code:
        return None, "no_code"
    if state is not None and state != expected_state:
        return None, "state_mismatch"
    return code, None


class WithingsGarminConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Withings to Garmin Sync."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._client_id: str = ""
        self._client_secret: str = ""
        self._callback_url: str = ""
        self._state: str = secrets.token_urlsafe(16)
        self._withings_token: dict[str, Any] | None = None
        self._garmin_email: str = ""
        self._garmin_login: garmin_api.GarminLoginResult | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return WithingsGarminOptionsFlow()

    def _withings_client(self) -> WithingsClient:
        return WithingsClient(
            async_get_clientsession(self.hass),
            self._client_id,
            self._client_secret,
            self._callback_url,
        )

    # --- Step 1: Withings developer app credentials -------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the Withings developer app credentials."""
        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID].strip()
            self._client_secret = user_input[CONF_CLIENT_SECRET].strip()
            self._callback_url = user_input[CONF_CALLBACK_URL].strip()
            return await self.async_step_withings_authorize()

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID, default=self._client_id): str,
                vol.Required(CONF_CLIENT_SECRET, default=self._client_secret): str,
                vol.Required(CONF_CALLBACK_URL, default=self._callback_url): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    # --- Step 2: authorize on Withings and paste the code back ----------------

    async def async_step_withings_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the authorization link and exchange the returned code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code, error = _extract_code(user_input.get(CONF_CODE, ""), self._state)
            if error:
                errors["base"] = error
            else:
                try:
                    token = await self._withings_client().request_token(code)
                except WithingsAuthError as err:
                    _LOGGER.warning("Withings rejected the authorization code: %s", err)
                    errors["base"] = "invalid_code"
                except WithingsError as err:
                    _LOGGER.error("Withings token exchange failed: %s", err)
                    errors["base"] = "cannot_connect"
                else:
                    self._withings_token = token
                    await self.async_set_unique_id(token["userid"])
                    if self.source == SOURCE_REAUTH:
                        self._abort_if_unique_id_mismatch(reason="wrong_account")
                    else:
                        self._abort_if_unique_id_configured()
                    return await self.async_step_garmin()

        return self.async_show_form(
            step_id="withings_authorize",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
            description_placeholders={
                "auth_url": self._withings_client().authorize_url(self._state),
                "callback_url": self._callback_url,
            },
        )

    # --- Step 3: Garmin credentials -------------------------------------------

    async def async_step_garmin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to Garmin Connect."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._garmin_email = user_input[CONF_EMAIL].strip()
            try:
                login = await self.hass.async_add_executor_job(
                    garmin_api.login_with_credentials,
                    self._garmin_email,
                    user_input[CONF_PASSWORD],
                )
            except garmin_api.GarminConnectAuthenticationError:
                errors["base"] = "invalid_auth"
            except garmin_api.GarminConnectTooManyRequestsError:
                errors["base"] = "rate_limited"
            except garmin_api.GarminConnectConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected Garmin login error")
                errors["base"] = "unknown"
            else:
                if login.mfa_required:
                    self._garmin_login = login
                    return await self.async_step_garmin_mfa()
                return await self._async_finish(login.tokens)

        return self.async_show_form(
            step_id="garmin",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=self._garmin_email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_garmin_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete Garmin multi-factor authentication."""
        errors: dict[str, str] = {}

        if user_input is not None and self._garmin_login is not None:
            try:
                tokens = await self.hass.async_add_executor_job(
                    garmin_api.complete_mfa,
                    self._garmin_login,
                    user_input[CONF_MFA_CODE].strip(),
                )
            except garmin_api.GarminConnectTooManyRequestsError:
                errors["base"] = "rate_limited"
            except garmin_api.GarminConnectConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Garmin MFA verification failed: %s", err)
                # The pending login may be consumed; go back and log in again.
                self._garmin_login = None
                return self.async_show_form(
                    step_id="garmin",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_EMAIL, default=self._garmin_email): str,
                            vol.Required(CONF_PASSWORD): str,
                        }
                    ),
                    errors={"base": "invalid_mfa"},
                )
            else:
                self._garmin_login = None
                return await self._async_finish(tokens)

        return self.async_show_form(
            step_id="garmin_mfa",
            data_schema=vol.Schema({vol.Required(CONF_MFA_CODE): str}),
            errors=errors,
            description_placeholders={"email": self._garmin_email},
        )

    async def _async_finish(self, garmin_tokens: str | None) -> ConfigFlowResult:
        assert self._withings_token is not None
        assert garmin_tokens
        data = {
            CONF_CLIENT_ID: self._client_id,
            CONF_CLIENT_SECRET: self._client_secret,
            CONF_CALLBACK_URL: self._callback_url,
            CONF_WITHINGS_TOKEN: self._withings_token,
            CONF_GARMIN_EMAIL: self._garmin_email,
            CONF_GARMIN_TOKENS: garmin_tokens,
        }
        if self.source == SOURCE_REAUTH:
            # data_updates merges, so last_sync is preserved
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        return self.async_create_entry(
            title=f"{NAME} ({self._garmin_email})",
            data=data,
            options={
                CONF_SYNC_INTERVAL: DEFAULT_SYNC_INTERVAL,
                CONF_FEATURES: list(DEFAULT_FEATURES),
            },
        )

    # --- Re-authentication ----------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when tokens are rejected."""
        self._client_id = entry_data[CONF_CLIENT_ID]
        self._client_secret = entry_data[CONF_CLIENT_SECRET]
        self._callback_url = entry_data[CONF_CALLBACK_URL]
        self._garmin_email = entry_data.get(CONF_GARMIN_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and restart the Withings + Garmin authorization."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_withings_authorize()


class WithingsGarminOptionsFlow(OptionsFlow):
    """Options: sync interval and features."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SYNC_INTERVAL,
                    default=options.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
                vol.Required(
                    CONF_FEATURES,
                    default=options.get(CONF_FEATURES, DEFAULT_FEATURES),
                ): cv.multi_select({f: f for f in ALL_FEATURES}),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
