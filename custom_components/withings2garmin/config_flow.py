"""Config flow for Withings to Garmin Sync."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers import config_validation as cv

from . import garmin as garmin_api
from .const import (
    ALL_FEATURES,
    CONF_FEATURES,
    CONF_GARMIN_EMAIL,
    CONF_GARMIN_TOKENS,
    CONF_SYNC_INTERVAL,
    DEFAULT_FEATURES,
    DEFAULT_SYNC_INTERVAL,
    DOMAIN,
    NAME,
    WITHINGS_SCOPE,
)

_LOGGER = logging.getLogger(__name__)

CONF_MFA_CODE = "mfa_code"


class WithingsGarminConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Withings OAuth (run by Home Assistant), then Garmin login."""

    DOMAIN = DOMAIN
    VERSION = 3

    def __init__(self) -> None:
        """Initialize the config flow."""
        super().__init__()
        self._oauth_data: dict[str, Any] | None = None
        self._garmin_email: str = ""
        self._garmin_login: garmin_api.GarminLoginResult | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Extra data appended to the Withings authorize URL."""
        return {"scope": WITHINGS_SCOPE}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return WithingsGarminOptionsFlow()

    # --- Withings OAuth finished ----------------------------------------------

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Withings is authorized; continue with Garmin."""
        await self.async_set_unique_id(str(data[CONF_TOKEN]["userid"]))
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
        else:
            self._abort_if_unique_id_configured()
        self._oauth_data = data
        return await self.async_step_garmin()

    # --- Garmin credentials -----------------------------------------------------

    def _garmin_form(self, errors: dict[str, str]) -> ConfigFlowResult:
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

        return self._garmin_form(errors)

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
                return self._garmin_form({"base": "invalid_mfa"})
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
        assert self._oauth_data is not None
        assert garmin_tokens
        data = {
            **self._oauth_data,  # auth_implementation + token
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
        self._garmin_email = entry_data.get(CONF_GARMIN_EMAIL, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm and restart the Withings + Garmin authorization."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()


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
