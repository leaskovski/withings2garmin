# Withings to Garmin Sync - Home Assistant Integration

A Home Assistant integration that syncs health data from Withings to Garmin Connect.

## Features

- **Withings OAuth2 Authentication** - Connect your Withings account
- **Garmin Authentication** - Connect your Garmin Connect account (supports MFA)
- **Manual Sync Service** - Trigger syncs via Home Assistant service
- **Background Sync** - Run syncs in background without blocking HA

## Installation

### Via HACS (Recommended)
1. Open Home Assistant
2. Go to HACS > Integrations
3. Click the "+" button
4. Search for "Withings to Garmin Sync"
5. Click Install

### Manual Installation
1. Copy the `withings_garmin` folder to your Home Assistant's `custom_components` folder
2. Restart Home Assistant

## Configuration

### Step 1: Add Integration
1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Withings to Garmin Sync"
4. Follow the configuration steps

### Step 2: Configure Withings
1. Enter your Withings OAuth credentials (or use defaults)
2. You will be redirected to Withings to authorize access
3. After authorization, you will be returned to HA

### Step 3: Configure Garmin
1. Enter your Garmin Connect username and password
2. If MFA is required, enter the code from your authenticator app
3. The integration will save your credentials securely

## Usage

### Trigger a Manual Sync

```yaml
service: withings_garmin.sync
data:
  features: "WEIGHT"
```

### Sync with Blood Pressure

```yaml
service: withings_garmin.sync
data:
  features: "WEIGHT,BLOOD_PRESSURE"
```

### Use in Automations

```yaml
automation:
  - trigger:
      - platform: time
        at: "08:00:00"
    action:
      - service: withings_garmin.sync
        data:
          features: "WEIGHT"
```

## Services

### withings_garmin.sync

Syncs data from Withings to Garmin.

| Parameter | Type | Description |
|-----------|------|-------------|
| features | string | Features to sync: WEIGHT, BLOOD_PRESSURE (comma-separated) |

## Supported Data

| Withings Data | Garmin Data |
|---------------|-------------|
| Weight | Weight (via activity upload) |
| Body Fat % | Body composition |
| Muscle Mass | Body composition |
| Bone Mass | Body composition |
| Hydration | Body composition |
| Blood Pressure | Blood pressure (via activity upload) |

## Troubleshooting

### Garmin MFA Issues
If you cannot complete MFA through the config flow:
1. Use the python-garminconnect library locally to generate tokens
2. Copy the token file to your HA config directory: `.withings_garmin_<entry_id>/garmin_session.json`

### No Data Synced
- Ensure your Withings account has measurements
- Check Home Assistant logs for errors
- Verify credentials are correctly stored

## Development

### Requirements
- Python 3.11+
- Home Assistant 2024.1+
- garminconnect>=0.3.2
- requests>=2.28.0
- aiohttp

### File Structure
```
withings_garmin/
├── __init__.py           # Integration entry point
├── manifest.json         # HA manifest
├── config_flow.py        # Config flow with MFA
├── const.py              # Constants
├── sync_engine.py        # Sync logic
└── README.md             # This file
```

## License

MIT License