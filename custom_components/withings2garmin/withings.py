"""Minimal async Withings API client."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .const import (
    WITHINGS_AUTHORIZE_URL,
    WITHINGS_MEASURE_URL,
    WITHINGS_SCOPE,
    WITHINGS_TOKEN_URL,
)

_LOGGER = logging.getLogger(__name__)

# Withings returns HTTP 200 with an error code in the JSON "status" field.
_AUTH_ERROR_STATUSES = {100, 101, 102, 200, 401}
_RATE_LIMIT_STATUS = 601
_TIMEOUT = aiohttp.ClientTimeout(total=30)


class WithingsError(Exception):
    """Generic Withings API error."""


class WithingsStatusError(WithingsError):
    """Withings answered with a non-zero API status."""

    def __init__(self, status: Any, message: str) -> None:
        super().__init__(message)
        self.status = status


class WithingsAuthError(WithingsStatusError):
    """Withings credentials/tokens are invalid and the user must re-authorize."""


class WithingsClient:
    """Talks to the Withings OAuth and measure endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        callback_url: str,
    ) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url

    def authorize_url(self, state: str) -> str:
        """Return the URL the user opens to grant access."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._callback_url,
                "scope": WITHINGS_SCOPE,
                "state": state,
            }
        )
        return f"{WITHINGS_AUTHORIZE_URL}?{query}"

    async def request_token(self, code: str) -> dict[str, Any]:
        """Exchange an authorization code for tokens."""
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._callback_url,
            }
        )

    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Use a refresh token to obtain a new access/refresh token pair."""
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        payload = {
            "action": "requesttoken",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            **data,
        }
        try:
            body = await self._post(WITHINGS_TOKEN_URL, payload)
        except WithingsAuthError:
            raise
        except WithingsStatusError as err:
            # The token endpoint rejecting a code/refresh token (e.g. status 503
            # "invalid params") means the user has to authorize again.
            if err.status == _RATE_LIMIT_STATUS:
                raise
            raise WithingsAuthError(err.status, str(err)) from err

        try:
            return {
                "access_token": body["access_token"],
                "refresh_token": body["refresh_token"],
                "userid": str(body["userid"]),
                "expires_at": int(time.time()) + int(body["expires_in"]),
            }
        except (KeyError, TypeError, ValueError) as err:
            raise WithingsError(f"Unexpected token response: {err}") from err

    async def get_measure_groups(
        self, access_token: str, lastupdate: int, meastypes: list[int]
    ) -> list[dict[str, Any]]:
        """Return all measure groups modified since `lastupdate`, following pagination."""
        groups: list[dict[str, Any]] = []
        offset: int | None = None
        while True:
            payload: dict[str, Any] = {
                "action": "getmeas",
                "meastypes": ",".join(str(t) for t in meastypes),
                "category": 1,  # real measurements only (2 = user objectives)
                "lastupdate": lastupdate,
            }
            if offset is not None:
                payload["offset"] = offset

            body = await self._post(
                WITHINGS_MEASURE_URL,
                payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            groups.extend(body.get("measuregrps", []))

            if not body.get("more"):
                break
            offset = body.get("offset")
            if offset is None:
                break
        return groups

    async def _post(
        self,
        url: str,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.post(
                url, data=data, headers=headers, timeout=_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise WithingsError(f"HTTP {resp.status} from Withings")
                result = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WithingsError(f"Error communicating with Withings: {err}") from err

        status = result.get("status")
        if status == 0:
            return result.get("body") or {}

        message = f"Withings API status {status}: {result.get('error', 'unknown error')}"
        if status in _AUTH_ERROR_STATUSES:
            raise WithingsAuthError(status, message)
        if status == _RATE_LIMIT_STATUS:
            message = f"Withings rate limit reached ({message})"
        raise WithingsStatusError(status, message)
