***NOTE! Whilst I can code, I decided to use AI to do all this component, so if you don't like AI Slop, then this component isn't for you***

# Withings to Garmin Sync - Home Assistant Integration

A Home Assistant custom integration that copies measurements from Withings to Garmin Connect.

## Features

- Uploads weigh-ins with body composition to Garmin: weight, body fat %, muscle mass, bone mass, hydration % and visceral fat.
- Can also upload blood pressure readings (systolic, diastolic and pulse).
- Syncs within seconds of a new measurement: Withings notifies Home Assistant through a webhook. You can also sync on demand with the `withings2garmin.sync` service.
- Checks itself: if no notification arrives for 24 hours, it runs a fallback sync and re-creates the Withings subscription if it's gone.
- Syncs incrementally. Only measurements created or edited since the last successful upload are sent. A failed upload is retried on the next sync, not skipped.
- Supports Garmin multi-factor authentication. Your Garmin password is used once to log in and is never stored.
- Refreshes Withings and Garmin tokens automatically. If a token is revoked, Home Assistant asks you to re-authenticate.

## Requirements

- Home Assistant 2024.11 or newer.
- A Withings developer application, which is free (see below).
- A Garmin Connect account.
- For instant syncs, Withings must be able to reach Home Assistant from the internet over HTTPS on port 443 (see [Notifications](#notifications)). Without that, the integration still syncs once every 24 hours.

## Installation

### HACS
1. HACS → Integrations → ⋮ → Custom repositories → add this repository as an *Integration*.
2. Install **Withings to Garmin Sync** and restart Home Assistant.

### Manual
Copy `custom_components/withings2garmin` into your Home Assistant `config/custom_components/` folder and restart.

## Configuration

Withings sign-in uses Home Assistant's built-in OAuth support (Application Credentials) and the [aiowithings](https://github.com/joostlek/python-withings) library, the same pieces the core Withings integration uses.

### 1. Create a Withings developer app
1. Sign in at <https://developer.withings.com/dashboard/> and create an application (*Public API integration*).
2. Set the **Callback URL** to `https://my.home-assistant.io/redirect/oauth`.
3. Note the **Client ID** and **Client Secret**.

You can reuse an app you already created for the core Withings integration, as long as that callback URL is registered. Each integration only manages its own notification subscriptions.

### 2. Add the integration
Settings → Devices & Services → Add Integration → **Withings to Garmin Sync**.

1. When asked, enter the Withings Client ID and Client Secret. They're saved under Settings → Devices & Services → ⋮ → Application credentials.
2. Sign in to Withings in the window that opens and grant access. You're sent back to Home Assistant automatically.
3. Enter your Garmin Connect email and password, then the verification code if Garmin asks for one.

> **Upgrading from 0.2.x:** older entries use a different sign-in method and won't load. Delete the entry, add the new callback URL to your Withings app, and add the integration again. Your Garmin history isn't affected. The first sync re-checks the last 7 days, so measurements from that period may be uploaded again.

### 3. Options
Click **Configure** on the integration to set:
- **Data to sync**: `WEIGHT` and/or `BLOOD_PRESSURE`. Only the matching Withings notifications are subscribed to.

The first sync uploads the last 7 days of measurements.

> **Upgrading from 0.3.x:** the *automatic sync interval* option is gone. Syncs are now triggered by Withings notifications, with a 24-hour fallback. Existing entries are upgraded automatically.

## Notifications

When Home Assistant starts (or the integration is reloaded), it subscribes to Withings notifications for the data you sync. Withings then calls Home Assistant whenever a new measurement arrives, and a sync runs about 10 seconds later. Notifications that arrive together, such as weight and blood pressure from one measurement, trigger a single sync. A catch-up sync also runs at startup.

Withings only calls public **HTTPS** URLs on **port 443**. The callback URL is chosen like this:
- **Home Assistant Cloud (Nabu Casa):** if you're subscribed, a cloudhook is used. Nothing else needs setting up.
- **Otherwise:** Home Assistant's **external URL** (Settings → System → Network) is used, for example through a reverse proxy. It must be `https://` with no port, or with port 443.

If there's no usable URL, a warning is logged and the integration falls back to syncing once every 24 hours.

**Watchdog:** if no notification arrives for 24 hours, the integration runs a sync, checks with Withings that the subscription still exists, and subscribes again if it doesn't. The 24-hour wait then starts over. If subscribing fails for a temporary reason, such as Withings being unable to reach the URL, it retries every 30 minutes.

## Service: `withings2garmin.sync`

| Field | Description |
|---|---|
| `config_entry_id` | Optional. Sync only this entry; by default every entry is synced. |
| `features` | Optional. A list (or comma-separated string) of `WEIGHT` and `BLOOD_PRESSURE`. Defaults to the options you set. |

The service returns a response with the number of records synced. If the sync fails, it raises an error.

```yaml
action: withings2garmin.sync
data:
  features:
    - WEIGHT
    - BLOOD_PRESSURE
response_variable: sync_result
```

## Data mapping

| Withings (meastype) | Garmin |
|---|---|
| Weight (1) | Weight |
| Fat ratio (6) | Body fat % |
| Muscle mass (76) | Muscle mass |
| Hydration (77, kg) | Body water %, calculated as hydration ÷ weight |
| Bone mass (88) | Bone mass |
| Visceral fat (170) | Visceral fat rating |
| Systolic / Diastolic / Pulse (10 / 9 / 11) | Blood pressure |

## Troubleshooting

- Turn on debug logging for `custom_components.withings2garmin` to see each upload, each notification received, and the subscription checks.
- **Syncs only happen once a day:** notifications aren't reaching Home Assistant. Check the log for a *Withings notifications are not set up* or *Withings rejected the notification callback URL* message, and make sure the URL works from outside your network over HTTPS on port 443.
- **Repeated Garmin login failures or rate limiting:** Garmin limits SSO logins. Wait before retrying.
- **Measurements edited in Withings** count as modified, so they're uploaded again and will appear in Garmin as an extra entry.

## Development

The conversion logic in `measurements.py` depends only on `aiowithings`. The sync engine and webhook tests also need Home Assistant and are skipped if it isn't installed:

```bash
pip install pytest aiowithings            # measurement tests
pip install homeassistant garminconnect   # also run the sync engine and webhook tests
pytest tests/
```

## License

MIT License
