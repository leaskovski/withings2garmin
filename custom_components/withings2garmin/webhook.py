"""Withings notification (webhook) handling.

Withings POSTs to a callback URL when new data is available; each notification
triggers a sync. Polling is kept only as a watchdog: if no notification arrives
within WEBHOOK_WATCHDOG, a fallback sync runs and the subscription is checked
(and re-created if Withings dropped it).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import ModuleType
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError
from aiohttp.hdrs import METH_POST
from aiohttp.web import Request
from aiowithings import (
    NotificationCategory,
    WithingsClient,
    WithingsError,
    WithingsInvalidParamsError,
)
from aiowithings.models import NotificationConfiguration
from yarl import URL

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_FEATURES,
    CONF_WEBHOOK_URL,
    DEFAULT_FEATURES,
    DOMAIN,
    FEATURE_BLOOD_PRESSURE,
    FEATURE_WEIGHT,
    NAME,
    WEBHOOK_RETRY_DELAY,
    WEBHOOK_SUBSCRIBE_DELAY,
    WEBHOOK_SYNC_DELAY,
    WEBHOOK_WATCHDOG,
)
from .sync_engine import SyncAuthError, WithingsGarminSync, translate_withings_auth_errors

if TYPE_CHECKING:
    from homeassistant.components.cloud import CloudConnectionState

_LOGGER = logging.getLogger(__name__)


def cloud_component() -> ModuleType:
    """Import the cloud component on first use.

    It is an after_dependency, so Home Assistant has normally imported it
    already; importing it at module level would pull its large dependency tree
    into anything that imports this integration (e.g. the tests).
    """
    from homeassistant.components import cloud  # noqa: PLC0415

    return cloud


# Withings notification category needed for each feature.
FEATURE_CATEGORIES: dict[str, NotificationCategory] = {
    FEATURE_WEIGHT: NotificationCategory.WEIGHT,
    FEATURE_BLOOD_PRESSURE: NotificationCategory.PRESSURE,
}

# Bound how long unloading the entry waits on Withings.
REVOKE_TIMEOUT = 15  # seconds


class WithingsWebhookManager:
    """Keeps the Withings subscription alive and turns notifications into syncs."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        withings: WithingsClient,
        engine: WithingsGarminSync,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.withings = withings
        self.engine = engine
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self._lock = asyncio.Lock()
        self._unsubs: list[CALLBACK_TYPE] = []
        self._cancel_watchdog: CALLBACK_TYPE | None = None
        self._cancel_retry: CALLBACK_TYPE | None = None
        self._url_warning_logged = False
        self._ha_webhook_registered = False
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=WEBHOOK_SYNC_DELAY,
            immediate=False,
            function=self._async_sync,
        )

    # --- configuration ----------------------------------------------------------

    @property
    def features(self) -> list[str]:
        """Features selected in the options."""
        return list(self.entry.options.get(CONF_FEATURES, DEFAULT_FEATURES))

    @property
    def categories(self) -> set[NotificationCategory]:
        """Withings notification categories needed for the selected features."""
        return {FEATURE_CATEGORIES[f] for f in self.features if f in FEATURE_CATEGORIES}

    # --- lifecycle --------------------------------------------------------------

    async def async_start(self) -> None:
        """Register the webhook in Home Assistant and subscribe once HA has started."""
        webhook.async_register(
            self.hass,
            DOMAIN,
            f"{NAME} ({self.entry.title})",
            self.webhook_id,
            self._async_handle_webhook,
            # HEAD (Withings' URL check) is answered by the webhook component.
            allowed_methods=[METH_POST],
        )
        self._ha_webhook_registered = True

        # Withings probes the callback URL while subscribing, so wait until HA
        # is serving requests.
        self._unsubs.append(async_at_started(self.hass, self._async_at_started))
        cloud = cloud_component()
        if cloud.async_is_logged_in(self.hass):
            self._unsubs.append(
                cloud.async_listen_connection_change(
                    self.hass, self._async_cloud_state_changed
                )
            )

    @callback
    def _async_at_started(self, _hass: HomeAssistant) -> None:
        self.entry.async_create_background_task(
            self.hass, self._async_initial_setup(), f"{DOMAIN} webhook setup"
        )

    async def _async_initial_setup(self) -> None:
        # Arm first so a failure below never leaves us without the fallback.
        self._arm_watchdog()
        await self.async_ensure_subscribed()
        # Catch anything that arrived while Home Assistant was not running.
        await self._async_sync()

    async def async_stop(self) -> None:
        """Stop listening and revoke our subscriptions (best effort)."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_timer("_cancel_watchdog")
        self._cancel_timer("_cancel_retry")
        self._debouncer.async_shutdown()
        if self._ha_webhook_registered:
            webhook.async_unregister(self.hass, self.webhook_id)
            self._ha_webhook_registered = False

        try:
            async with asyncio.timeout(REVOKE_TIMEOUT):
                async with self._lock:
                    configs = await self.withings.list_notification_configurations()
                    for config in configs:
                        if self._is_ours(config, None):
                            await self._async_revoke(config)
        except (TimeoutError, WithingsError, ClientError, SyncAuthError) as err:
            _LOGGER.debug("Could not revoke Withings notifications on unload: %s", err)
        except Exception:  # noqa: BLE001 - never block unloading
            _LOGGER.debug("Could not revoke Withings notifications", exc_info=True)

    @callback
    def _cancel_timer(self, attr: str) -> None:
        cancel: CALLBACK_TYPE | None = getattr(self, attr)
        if cancel is not None:
            cancel()
            setattr(self, attr, None)

    # --- callback URL -----------------------------------------------------------

    async def async_get_url(self) -> str | None:
        """Return the public callback URL, or None if there is no usable one."""
        cloud = cloud_component()
        if cloud.async_active_subscription(self.hass):
            if not cloud.async_is_connected(self.hass):
                # Subscribed again once the cloud connection comes up.
                _LOGGER.debug("Waiting for Home Assistant Cloud to connect")
                return None
            try:
                url = await cloud.async_get_or_create_cloudhook(
                    self.hass, self.webhook_id
                )
            except (cloud.CloudNotAvailable, cloud.CloudNotConnected) as err:
                _LOGGER.debug("Cloudhook not available: %s", err)
                return None
        else:
            try:
                url = webhook.async_generate_url(
                    self.hass, self.webhook_id, allow_internal=False
                )
            except NoURLAvailableError:
                self._warn_url_once(
                    "no external URL is configured and Home Assistant Cloud is "
                    "not active"
                )
                return None

        parsed = URL(url)
        if parsed.scheme != "https" or parsed.port != 443:
            self._warn_url_once(f"{parsed.origin()} is not HTTPS on port 443")
            return None
        self._url_warning_logged = False
        return url

    def _warn_url_once(self, reason: str) -> None:
        if not self._url_warning_logged:
            _LOGGER.warning(
                "Withings notifications are not set up because %s. Withings can "
                "only call a public HTTPS URL on port 443. Falling back to a sync "
                "every %s",
                reason,
                WEBHOOK_WATCHDOG,
            )
            self._url_warning_logged = True

    # --- subscription -----------------------------------------------------------

    def _is_ours(
        self, config: NotificationConfiguration, current_url: str | None
    ) -> bool:
        """Whether a Withings subscription was made by this config entry.

        Leaves alone subscriptions other integrations made with the same
        Withings app.
        """
        url = config.callback_url
        return (
            url == current_url
            or url == self.entry.data.get(CONF_WEBHOOK_URL)
            or self.webhook_id in url
        )

    async def _async_revoke(self, config: NotificationConfiguration) -> None:
        _LOGGER.debug(
            "Revoking Withings notification %s for %s",
            config.notification_category.name,
            config.callback_url,
        )
        with translate_withings_auth_errors():
            await self.withings.revoke_notification_configurations(
                config.callback_url, config.notification_category
            )

    async def async_ensure_subscribed(self) -> bool:
        """Make Withings' subscriptions match what we need. Returns True if active."""
        async with self._lock:
            self._cancel_timer("_cancel_retry")
            url = await self.async_get_url()
            if url is None:
                return False

            needed = self.categories
            try:
                with translate_withings_auth_errors():
                    configs = await self.withings.list_notification_configurations()

                have: set[NotificationCategory] = set()
                for config in configs:
                    if not self._is_ours(config, url):
                        continue
                    if config.callback_url == url and config.notification_category in needed:
                        have.add(config.notification_category)
                    else:
                        # Old URL, or a category for a feature that was turned off.
                        await self._async_revoke(config)

                missing = sorted(needed - have)
                for index, category in enumerate(missing):
                    if index:
                        await asyncio.sleep(WEBHOOK_SUBSCRIBE_DELAY)
                    _LOGGER.debug("Subscribing %s for %s", url, category.name)
                    with translate_withings_auth_errors():
                        await self.withings.subscribe_notification(url, category)
            except SyncAuthError as err:
                _LOGGER.error("Withings notifications need re-authentication: %s", err)
                self.entry.async_start_reauth(self.hass)
                return False
            except WithingsInvalidParamsError as err:
                # Typically Withings could not reach the callback URL.
                _LOGGER.error(
                    "Withings rejected the notification callback URL %s: %s. "
                    "Retrying in %s",
                    url,
                    err,
                    WEBHOOK_RETRY_DELAY,
                )
                self._schedule_retry()
                return False
            except (WithingsError, ClientError) as err:
                _LOGGER.warning(
                    "Subscribing to Withings notifications failed: %s. Retrying in %s",
                    err,
                    WEBHOOK_RETRY_DELAY,
                )
                self._schedule_retry()
                return False

            if self.entry.data.get(CONF_WEBHOOK_URL) != url:
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, CONF_WEBHOOK_URL: url}
                )
            if missing:
                _LOGGER.info(
                    "Subscribed to Withings notifications (%s)",
                    ", ".join(c.name for c in missing),
                )
            else:
                _LOGGER.debug("Withings notification subscription is up to date")
            return True

    @callback
    def _schedule_retry(self) -> None:
        self._cancel_timer("_cancel_retry")
        self._cancel_retry = async_call_later(
            self.hass, WEBHOOK_RETRY_DELAY, self._async_retry
        )

    async def _async_retry(self, _now: datetime) -> None:
        self._cancel_retry = None
        await self.async_ensure_subscribed()

    async def _async_cloud_state_changed(self, state: CloudConnectionState) -> None:
        if state is cloud_component().CloudConnectionState.CLOUD_CONNECTED:
            _LOGGER.debug("Home Assistant Cloud connected; checking subscription")
            if await self.async_ensure_subscribed():
                # Notifications may have been missed while disconnected.
                await self._debouncer.async_call()

    # --- notifications ----------------------------------------------------------

    async def _async_handle_webhook(
        self, hass: HomeAssistant, webhook_id: str, request: Request
    ) -> None:
        """Handle a Withings notification. Withings only needs a 200 response."""
        params = await request.post()
        user_id = params.get("userid")
        appli = params.get("appli")
        try:
            category = NotificationCategory(int(str(appli)))
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring Withings notification with appli=%r", appli)
            return None

        if str(user_id) != self.entry.unique_id:
            _LOGGER.debug("Ignoring Withings notification for user %s", user_id)
            return None
        if category not in self.categories:
            _LOGGER.debug("Ignoring Withings notification for %s", category.name)
            return None

        _LOGGER.debug("Withings notification received for %s", category.name)
        self._arm_watchdog()
        await self._debouncer.async_call()
        return None

    @callback
    def _arm_watchdog(self) -> None:
        self._cancel_timer("_cancel_watchdog")
        self._cancel_watchdog = async_call_later(
            self.hass, WEBHOOK_WATCHDOG, self._async_watchdog_fired
        )

    async def _async_watchdog_fired(self, _now: datetime) -> None:
        self._cancel_watchdog = None
        self._arm_watchdog()
        _LOGGER.info(
            "No Withings notification in %s; running a fallback sync and checking "
            "the subscription",
            WEBHOOK_WATCHDOG,
        )
        await self._async_sync()
        await self.async_ensure_subscribed()

    async def _async_sync(self) -> None:
        result: dict[str, Any] = await self.engine.async_sync(self.features)
        _LOGGER.debug("Sync result: %s", result)
