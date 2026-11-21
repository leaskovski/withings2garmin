"""Tests for the Withings notification (webhook) manager.

Home Assistant's webhook/cloud components, the debouncer and timers are mocked,
so only the manager's own logic is exercised. Requires Home Assistant and
aiowithings to be installed; skipped otherwise.
"""

import asyncio
import sys
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")
aiowithings = pytest.importorskip("aiowithings")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.withings2garmin import webhook as wh  # noqa: E402
from custom_components.withings2garmin.const import (  # noqa: E402
    CONF_WEBHOOK_URL,
    WEBHOOK_RETRY_DELAY,
    WEBHOOK_WATCHDOG,
)

Cat = aiowithings.NotificationCategory
HOOK_ID = "abc123hook"
URL = f"https://ha.example.com/api/webhook/{HOOK_ID}"
USER_ID = "4242"


def _config(url, category):
    return SimpleNamespace(callback_url=url, notification_category=category)


class Harness:
    """Builds a manager with every Home Assistant dependency mocked."""

    def __init__(
        self,
        features=("WEIGHT",),
        configs=(),
        url=URL,
        stored_url=None,
        cloud=None,
        list_error=None,
    ):
        data = {"webhook_id": HOOK_ID}
        if stored_url:
            data[CONF_WEBHOOK_URL] = stored_url
        self.entry = SimpleNamespace(
            data=data,
            options={"features": list(features)},
            unique_id=USER_ID,
            title="Test",
            async_start_reauth=MagicMock(),
        )

        def _update_entry(entry, data):
            entry.data = data

        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_update_entry=_update_entry)
        )
        self.withings = MagicMock()
        self.withings.list_notification_configurations = AsyncMock(
            return_value=list(configs), side_effect=list_error
        )
        self.withings.subscribe_notification = AsyncMock()
        self.withings.revoke_notification_configurations = AsyncMock()
        self.engine = SimpleNamespace(async_sync=AsyncMock(return_value={"success": True}))

        self.ha_webhook = MagicMock()
        self.ha_webhook.async_generate_url = MagicMock(return_value=url)
        self.cloud = cloud or SimpleNamespace(
            async_active_subscription=lambda hass: False,
            async_is_logged_in=lambda hass: False,
        )
        self.call_later = MagicMock(side_effect=lambda *a: MagicMock())
        self.debouncer = MagicMock()
        self.debouncer.async_call = AsyncMock()

        self._stack = ExitStack()
        for target, value in (
            ("webhook", self.ha_webhook),
            ("cloud_component", lambda: self.cloud),
            ("async_call_later", self.call_later),
            ("Debouncer", MagicMock(return_value=self.debouncer)),
            ("WEBHOOK_SUBSCRIBE_DELAY", 0),
        ):
            self._stack.enter_context(patch.object(wh, target, value))
        self.manager = wh.WithingsWebhookManager(
            self.hass, self.entry, self.withings, self.engine
        )

    def run(self, coro):
        return asyncio.run(coro)

    def close(self):
        self._stack.close()

    def delays(self):
        return [c.args[1] for c in self.call_later.call_args_list]


@pytest.fixture
def harness():
    created = []

    def _make(**kwargs):
        h = Harness(**kwargs)
        created.append(h)
        return h

    yield _make
    for h in created:
        h.close()


def _request(**params):
    return SimpleNamespace(post=AsyncMock(return_value=params))


# --- notifications ---------------------------------------------------------------


def test_notification_schedules_sync_and_resets_watchdog(harness):
    h = harness()
    h.run(h.manager._async_handle_webhook(h.hass, HOOK_ID, _request(userid=USER_ID, appli="1")))
    h.debouncer.async_call.assert_awaited_once()
    assert h.delays() == [WEBHOOK_WATCHDOG]


@pytest.mark.parametrize(
    "params",
    [
        {"userid": "9999", "appli": "1"},  # different Withings user
        {"userid": USER_ID, "appli": "4"},  # blood pressure not selected
        {"userid": USER_ID, "appli": "garbage"},
        {"userid": USER_ID},
    ],
)
def test_irrelevant_notifications_are_ignored(harness, params):
    h = harness(features=("WEIGHT",))
    h.run(h.manager._async_handle_webhook(h.hass, HOOK_ID, _request(**params)))
    h.debouncer.async_call.assert_not_awaited()
    h.call_later.assert_not_called()


def test_blood_pressure_notification_when_selected(harness):
    h = harness(features=("WEIGHT", "BLOOD_PRESSURE"))
    h.run(h.manager._async_handle_webhook(h.hass, HOOK_ID, _request(userid=USER_ID, appli="4")))
    h.debouncer.async_call.assert_awaited_once()


# --- subscription ----------------------------------------------------------------


def test_subscribes_missing_categories_and_stores_url(harness):
    h = harness(features=("WEIGHT", "BLOOD_PRESSURE"), configs=[_config(URL, Cat.WEIGHT)])
    assert h.run(h.manager.async_ensure_subscribed())
    h.withings.subscribe_notification.assert_awaited_once_with(URL, Cat.PRESSURE)
    h.withings.revoke_notification_configurations.assert_not_awaited()
    assert h.entry.data[CONF_WEBHOOK_URL] == URL


def test_already_subscribed_is_a_noop(harness):
    h = harness(configs=[_config(URL, Cat.WEIGHT)], stored_url=URL)
    assert h.run(h.manager.async_ensure_subscribed())
    h.withings.subscribe_notification.assert_not_awaited()
    h.withings.revoke_notification_configurations.assert_not_awaited()


def test_revokes_our_stale_subscriptions_but_not_others(harness):
    old_url = "https://hooks.nabu.casa/old-token"
    other = _config("https://someone-else.example.com/hook", Cat.WEIGHT)
    h = harness(
        features=("WEIGHT",),
        stored_url=old_url,
        configs=[
            _config(old_url, Cat.WEIGHT),  # our previous URL
            _config(URL, Cat.PRESSURE),  # our URL, feature no longer selected
            other,
        ],
    )
    assert h.run(h.manager.async_ensure_subscribed())
    revoked = [c.args for c in h.withings.revoke_notification_configurations.call_args_list]
    assert sorted(revoked) == sorted([(old_url, Cat.WEIGHT), (URL, Cat.PRESSURE)])
    h.withings.subscribe_notification.assert_awaited_once_with(URL, Cat.WEIGHT)


@pytest.mark.parametrize(
    "url", ["http://ha.example.com/api/webhook/x", "https://ha.example.com:8123/api/webhook/x"]
)
def test_unusable_url_skips_subscription(harness, url):
    h = harness(url=url)
    assert not h.run(h.manager.async_ensure_subscribed())
    h.withings.list_notification_configurations.assert_not_awaited()


def test_uses_cloudhook_when_cloud_is_active(harness):
    cloud_url = "https://hooks.nabu.casa/token"
    cloud = SimpleNamespace(
        async_active_subscription=lambda hass: True,
        async_is_connected=lambda hass: True,
        async_get_or_create_cloudhook=AsyncMock(return_value=cloud_url),
        CloudNotAvailable=type("CloudNotAvailable", (Exception,), {}),
        CloudNotConnected=type("CloudNotConnected", (Exception,), {}),
    )
    h = harness(cloud=cloud)
    assert h.run(h.manager.async_ensure_subscribed())
    h.withings.subscribe_notification.assert_awaited_once_with(cloud_url, Cat.WEIGHT)
    h.ha_webhook.async_generate_url.assert_not_called()


def test_auth_error_starts_reauth(harness):
    h = harness(list_error=aiowithings.WithingsUnauthorizedError("nope"))
    assert not h.run(h.manager.async_ensure_subscribed())
    h.entry.async_start_reauth.assert_called_once()
    assert h.delays() == []


def test_transient_error_schedules_retry(harness):
    h = harness(list_error=aiowithings.WithingsConnectionError("down"))
    assert not h.run(h.manager.async_ensure_subscribed())
    h.entry.async_start_reauth.assert_not_called()
    assert h.delays() == [WEBHOOK_RETRY_DELAY]


# --- watchdog --------------------------------------------------------------------


def test_watchdog_syncs_resubscribes_and_rearms(harness):
    h = harness(configs=[])  # Withings dropped our subscription
    h.run(h.manager._async_watchdog_fired(None))
    h.engine.async_sync.assert_awaited_once_with(["WEIGHT"])
    h.withings.subscribe_notification.assert_awaited_once_with(URL, Cat.WEIGHT)
    assert h.delays() == [WEBHOOK_WATCHDOG]


def test_watchdog_still_syncs_without_public_url(harness):
    h = harness(url="http://homeassistant.local:8123/api/webhook/x")
    h.run(h.manager._async_watchdog_fired(None))
    h.engine.async_sync.assert_awaited_once()
    h.withings.subscribe_notification.assert_not_awaited()
    assert h.delays() == [WEBHOOK_WATCHDOG]


# --- unload ----------------------------------------------------------------------


def test_stop_revokes_only_our_subscriptions(harness):
    other = _config("https://someone-else.example.com/hook", Cat.WEIGHT)
    h = harness(configs=[_config(URL, Cat.WEIGHT), other], stored_url=URL)
    h.manager._ha_webhook_registered = True
    h.run(h.manager.async_stop())
    h.withings.revoke_notification_configurations.assert_awaited_once_with(URL, Cat.WEIGHT)
    h.ha_webhook.async_unregister.assert_called_once_with(h.hass, HOOK_ID)
    h.debouncer.async_shutdown.assert_called_once()
