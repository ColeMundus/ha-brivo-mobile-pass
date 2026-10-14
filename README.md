# Brivo Mobile Pass for Home Assistant

A custom integration that opens authorized Brivo Mobile Pass gates and doors directly from Home Assistant. No separate server, bridge service, MQTT broker, or phone app is needed for each unlock. Requires Home Assistant 2026.10 or newer and internet access to Brivo.

This is an independent community integration. It uses the Mobile Pass cloud endpoints observed in the Android app, which may change. It is not the Brivo administrator Open API integration. Bluetooth, NFC, PIN entry, location spoofing, physical gate state, and Google/Firebase sign-in are outside its scope.

## What it does

- Discovers the Mobile Pass credential, sites, and enabled access points.
- Creates a native **Open** button for each discovered access point.
- Uses Bearer authentication and refreshes access tokens on demand.
- Saves rotated refresh tokens before sending an unlock.
- Supports existing refresh tokens or one-time invitation setup through the UI.
- Supports multiple passes, removal, reauthentication, and redacted diagnostics.
- Offers `brivo_mobile_pass.check_connection`, which verifies authentication without opening anything.

Buttons represent commands. A successful request means Brivo accepted the command; there is no sensor confirming that a gate moved. There is no background polling. Discovery occurs during setup; newly granted access points can be rediscovered by reauthenticating. You can disable unwanted entities in Home Assistant's entity settings.

## Installation

### HACS custom repository

After this repository is published:

1. Open HACS → Custom repositories.
2. Add `https://github.com/ColeMundus/ha-brivo-mobile-pass`, category **Integration**.
3. Download **Brivo Mobile Pass** and restart Home Assistant.
4. Go to Settings → Devices & services → Add integration → **Brivo Mobile Pass**.

HACS custom-repository installation is separate from inclusion in the default HACS catalog. See [publishing](docs/PUBLISHING.md).

### Manual

Copy `custom_components/brivo_mobile_pass` into your HA configuration's `custom_components` directory, restart HA, and add the integration through Devices & services.

## Credentials and setup

You need matching **OAuth client ID and client secret** for the Mobile Pass endpoints, plus either an existing Mobile Pass refresh token or a fresh invitation code and its email address. These values are not shipped in this repository. The OAuth application credentials alone do not grant access; a user Mobile Pass is still required. An administrator Open API key is not automatically interchangeable with Mobile Pass application credentials.

Use application credentials obtained through an authorized source. An existing refresh token must be exported from an account/session you control; this integration does not extract a phone's private storage. New installations can use an invitation supplied by the property/access administrator, provided they also have matching application credentials. There is currently no browser OAuth login or automatic public client-credential provisioning. This prerequisite limits plug-and-play setup for other users.

Choose **Use an existing refresh token** when migrating an authenticated client. Choose **Redeem a new invitation once** for a new pass. Never paste credentials into issues, screenshots, or GitHub.

Invitation submission is explicit and is never automatically retried. HA records the attempt before redemption and saves returned tokens before discovery. If discovery fails or you cancel, submitting the same setup values recovers saved tokens. If the redemption failed with no saved tokens, the same attempt is blocked because its result could be uncertain; use a saved refresh token or obtain another invitation. A process/network failure between server redemption and receiving/saving the response cannot be made transactional with Brivo.

Tokens and setup recovery records live in HA's private `.storage` directory. They are not encrypted independently of HA's storage. Protect your HA configuration and backups. A pending/cancelled setup may leave a recovery record; successful setup removes its record. Deleting an installed integration removes its live token store. Invitations are stored only as hashed attempt identifiers, not plaintext codes.

## Use in automations

Use the actual button entity ID shown in HA:

```yaml
actions:
  - action: button.press
    target:
      entity_id: button.example_gate_open
```

Read-only connection check:

```yaml
actions:
  - action: brivo_mobile_pass.check_connection
```

With multiple passes, provide `config_entry_id` using the action's integration selector. Your own arrival detection and repeat interval belong in normal HA automations; the integration does not start repeated unlocks itself.

## Troubleshooting

- **Expired/revoked credentials:** HA requests reauthentication. Supply an authorized refresh token or a fresh invitation. Existing entity IDs remain stable across reauthentication for the same access-point IDs.
- **HTTP 403:** Brivo refused the operation. Confirm that your pass permits remote activation. A gate may require proximity, two-factor, or other policy checks that this integration does not implement.
- **Network failure:** The command is not automatically retried, because Brivo may already have received it. Check the physical gate before retrying manually.
- **Missing access point:** Only discovered, enabled access points are exposed. Reauthenticate to rediscover changed permissions.
- **Cannot save credentials:** No unlock is sent until the refreshed token is saved. Check HA storage capacity and permissions.

The unlock body currently supplies an empty Wi-Fi proximity list. This works at the tested site; sites requiring real proximity evidence may reject it. A live test at one site confirmed server acceptance of a left-gate command; physical movement was not independently observed. Broader hardware/site compatibility needs community testing.

## Development

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests use fabricated credentials and HA's real integration test harness. CI runs tests, Ruff, Hassfest, and HACS validation. The API client is kept in a separate module without HA imports; there is no extra runtime package to publish or install. The manifest uses `cloud_polling` because HA has no cloud-command-only IoT class; the implementation does not poll.

See [architecture and API](docs/ARCHITECTURE.md), [publishing](docs/PUBLISHING.md), and [security](SECURITY.md). No Home Assistant quality-scale certification is claimed.
