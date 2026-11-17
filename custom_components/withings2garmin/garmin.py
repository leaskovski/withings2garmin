"""Blocking helpers around the garminconnect library.

Every function here performs network I/O synchronously and MUST be run in the
executor (hass.async_add_executor_job), never directly in the event loop.
"""

from __future__ import annotations

from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from .measurements import BloodPressure, BodyComposition

__all__ = [
    "Garmin",
    "GarminConnectAuthenticationError",
    "GarminConnectConnectionError",
    "GarminConnectTooManyRequestsError",
    "GarminLoginResult",
    "add_blood_pressure",
    "add_body_composition",
    "complete_mfa",
    "login_with_credentials",
    "login_with_tokens",
]


class GarminLoginResult:
    """Outcome of a credential login."""

    def __init__(
        self,
        client: Garmin,
        tokens: str | None = None,
        mfa_state: Any = None,
    ) -> None:
        self.client = client
        self.tokens = tokens
        self.mfa_state = mfa_state

    @property
    def mfa_required(self) -> bool:
        return self.mfa_state is not None


def login_with_credentials(email: str, password: str) -> GarminLoginResult:
    """Log in with email/password. Returns MFA state instead of blocking for a code."""
    client = Garmin(email, password, return_on_mfa=True)
    result = client.login()
    if isinstance(result, tuple) and len(result) == 2 and result[0] == "needs_mfa":
        return GarminLoginResult(client, mfa_state=result[1])
    return GarminLoginResult(client, tokens=client.client.dumps())


def complete_mfa(login: GarminLoginResult, mfa_code: str) -> str:
    """Finish an MFA login and return the serialized token string."""
    login.client.resume_login(login.mfa_state, mfa_code)
    return login.client.client.dumps()


def login_with_tokens(tokens: str) -> tuple[Garmin, str]:
    """Log in from stored tokens.

    garminconnect refreshes the tokens if they are close to expiry, so the
    (possibly new) serialized tokens are returned and should be persisted.
    """
    client = Garmin()
    client.login(tokens)
    return client, client.client.dumps()


def add_body_composition(client: Garmin, data: BodyComposition, timestamp: str) -> None:
    """Upload one weigh-in."""
    client.add_body_composition(
        timestamp,
        weight=data.weight,
        percent_fat=data.percent_fat,
        percent_hydration=data.percent_hydration,
        bone_mass=data.bone_mass,
        muscle_mass=data.muscle_mass,
        visceral_fat_rating=data.visceral_fat_rating,
    )


def add_blood_pressure(client: Garmin, data: BloodPressure, timestamp: str) -> None:
    """Upload one blood pressure reading."""
    client.set_blood_pressure(
        systolic=data.systolic,
        diastolic=data.diastolic,
        pulse=data.pulse,
        timestamp=timestamp,
        notes="Synced from Withings",
    )
