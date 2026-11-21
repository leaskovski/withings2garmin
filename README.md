***NOTE! Whilst I can code, I decided to use AI to do all this component, so if you don't like AI Slop, then this component isn't for you***

# Withings to Garmin Sync - Home Assistant Integration

A Home Assistant custom integration that copies measurements from Withings to Garmin Connect.

## Features

- Uploads weigh-ins with body composition to Garmin: weight, body fat %, muscle mass, bone mass, hydration % and visceral fat.
- Can also upload blood pressure readings (systolic, diastolic and pulse).
- Syncs automatically on a configurable interval, or on demand with the `withings2garmin.sync` service.
- Syncs incrementally. Only measurements created or edited since the last successful upload are sent. A failed upload is retried on the next sync, not skipped.
- Supports Garmin multi-factor authentication. Your Garmin password is used once to log in and is never stored.
- Refreshes Withings and Garmin tokens automatically. If a token is revoked, Home Assistant asks you to re-authenticate.

## Requirements

- Home Assistant 2024.11 or newer.
- A Withings developer application, which is free (see below).
- A Garmin Connect account.

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

You can reuse an app you already created for the core Withings integration, as long as that callback URL is registered.

### 2. Add the integration
Settings → Devices & Services → Add Integration → **Withings to Garmin Sync**.

1. When asked, enter the Withings Client ID and Client Secret. They're saved under Settings → Devices & Services → ⋮ → Application credentials.
2. Sign in to Withings in the window that opens and grant access. You're sent back to Home Assistant automatically.
3. Enter your Garmin Connect email and password, then the verification code if Garmin asks for one.

> **Upgrading from 0.2.x:** older entries use a different sign-in method and won't load. Delete the entry, add the new callback URL to your Withings app, and add the integration again. Your Garmin history isn't affected. The first sync re-checks the last 7 days, so measurements from that period may be uploaded again.

### 3. Options
Click **Configure** on the integration to set:
- **Automatic sync interval** in minutes. The default is 60; use `0` to sync only via the service.
- **Data to sync**: `WEIGHT` and/or `BLOOD_PRESSURE`.

The first sync uploads the last 7 days of measurements.

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

Example: sync shortly after a weigh-in, triggered by the core Withings integration's weight sensor:

```yaml
automation:
  - alias: Push new weigh-ins to Garmin
    triggers:
      - trigger: state
        entity_id: sensor.withings_weight
    actions:
      - delay: "00:01:00"
      - action: withings2garmin.sync
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

- Turn on debug logging for `custom_components.withings2garmin` to see each upload.
- **Repeated Garmin login failures or rate limiting:** Garmin limits SSO logins. Wait before retrying.
- **Measurements edited in Withings** count as modified, so they're uploaded again and will appear in Garmin as an extra entry.

## Development

The conversion logic in `measurements.py` depends only on `aiowithings`. The sync engine tests also need Home Assistant and are skipped if it isn't installed:

```bash
pip install pytest aiowithings            # measurement tests
pip install homeassistant garminconnect   # also run the sync engine tests
pytest tests/
```

## License

MIT License
