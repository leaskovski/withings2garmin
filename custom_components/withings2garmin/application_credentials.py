"""Application credentials platform for Withings to Garmin Sync.

Home Assistant runs the OAuth2 flow and token refresh; this module only adapts
Withings' non-standard token endpoint. Mirrors Home Assistant core's
`withings/application_credentials.py`.
"""

from __future__ import annotations

from typing import Any

from aiohttp import ClientResponseError, RequestInfo
from aiowithings import AUTHORIZATION_URL, TOKEN_URL
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

_RATE_LIMIT_STATUS = 601


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return auth implementation."""
    return WithingsLocalOAuth2Implementation(
        hass,
        DOMAIN,
        credential,
        authorization_server=AuthorizationServer(
            authorize_url=AUTHORIZATION_URL,
            token_url=TOKEN_URL,
        ),
    )


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return description placeholders for the credentials dialog."""
    return {
        "developer_dashboard_url": "https://developer.withings.com/dashboard/",
        "redirect_url": "https://my.home-assistant.io/redirect/oauth",
    }


class WithingsLocalOAuth2Implementation(AuthImplementation):
    """OAuth2 implementation for Withings' token endpoint."""

    async def _token_request(self, data: dict) -> dict:
        """Make a token request and adapt the Withings reply.

        Withings wraps the token in {"status": 0, "body": {...}} and reports
        errors with HTTP 200 and a non-zero status. Errors are re-raised as HTTP
        errors so Home Assistant's OAuth handling treats them correctly:
        400 -> re-authentication needed, 429 -> try again later.
        """
        response = await super()._token_request(data)
        status = response.get("status", 0)
        if status != 0:
            raise ClientResponseError(
                RequestInfo(
                    URL(TOKEN_URL), "POST", CIMultiDictProxy(CIMultiDict()), URL(TOKEN_URL)
                ),
                (),
                status=429 if status == _RATE_LIMIT_STATUS else 400,
                message=f"Withings token request failed with status {status}: "
                f"{response.get('error', 'unknown error')}",
            )
        if body := response.pop("body", None):
            response.update(body)
        return response

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        """Resolve the authorization code to tokens."""
        return await self._token_request(
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": external_data["state"]["redirect_uri"],
            }
        )

    async def _async_refresh_token(self, token: dict) -> dict:
        """Refresh tokens. Withings rotates the refresh token on every refresh."""
        new_token = await self._token_request(
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": token["refresh_token"],
            }
        )
        return {**token, **new_token}
