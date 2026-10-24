<img src="https://raw.githubusercontent.com/ColeMundus/ha-brivo-mobile-pass/main/custom_components/brivo_mobile_pass/brand/icon.png" alt="Brivo Mobile Pass" width="80">

# Brivo Mobile Pass for Home Assistant

[![Checks](https://github.com/ColeMundus/ha-brivo-mobile-pass/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/ColeMundus/ha-brivo-mobile-pass/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-default-41BDF5?logo=homeassistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-brivo-mobile-pass&category=integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-41BDF5?logo=homeassistant&logoColor=white)](https://github.com/ColeMundus/ha-brivo-mobile-pass/blob/main/custom_components/brivo_mobile_pass/manifest.json)
[![Release](https://img.shields.io/github/v/release/ColeMundus/ha-brivo-mobile-pass)](https://github.com/ColeMundus/ha-brivo-mobile-pass/releases/latest)
[MIT license](https://github.com/ColeMundus/ha-brivo-mobile-pass/blob/main/LICENSE)

Open Brivo Mobile Pass gates and doors from Home Assistant. The integration adds an **Open** button for each enabled access point on your pass.

Requires **Home Assistant 2026.10 or later**, internet access, and a valid Brivo Mobile Pass. Built-in setup uses the US production Mobile Pass client. No APK download, extraction tools, or developer account is needed.

## Install

### HACS

1. [Open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=ColeMundus&repository=ha-brivo-mobile-pass&category=integration), or search HACS for **Brivo Mobile Pass**.
2. Download the integration and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Brivo Mobile Pass**.

### Manual installation

Copy `custom_components/brivo_mobile_pass` into Home Assistant's `custom_components` directory, restart Home Assistant, and add the integration through **Devices & services**.

## Set up

### New invitation

Select **New invitation**, then enter the email address that received the invitation and its unused invitation code. The integration supplies the app credentials automatically.

Submitting the form activates the pass and consumes the invitation. An invitation already used in the mobile app cannot be used again; request a new invitation from your property administrator, or use an existing refresh token instead.

If setup fails after activation, submit the same details to resume using the saved tokens. If activation was attempted but no tokens were saved, setup will not risk redeeming the invitation again.

### Existing pass

Select **Existing refresh token** and enter its refresh token. Tokens must belong to the bundled US Mobile Pass client. If setup reports a different client, use **Advanced** to supply the matching client ID and secret manually.

Advanced setup also supports invitations with manually supplied app credentials. It does not change the integration's US API endpoints or add support for other regions. Do not paste personal tokens into online decoders or issue reports.

### Upgrading

Existing installations keep their saved app credentials and tokens; no setup changes are required. Reauthentication uses the app credentials already saved for that pass unless you explicitly choose manual credentials.

Home Assistant saves the credentials and refreshes access tokens automatically. Protect your Home Assistant configuration and backups, which contain these credentials.

## Use

Press an access point's **Open** button to send an unlock request. You can add these buttons to a dashboard or use them in scripts and automations.

```yaml
actions:
  - action: button.press
    target:
      entity_id: button.example_gate_open
```

Replace `button.example_gate_open` with the entity ID shown in Home Assistant.

Buttons send commands; they do not report whether a gate is physically open or closed. Disable any buttons you don't want through Home Assistant's entity settings.

To check the connection without opening a gate, run `brivo_mobile_pass.check_connection`. If you have multiple passes, select the integration in the action's **Integration** field.

## Troubleshooting

- **Expired or revoked pass:** Follow Home Assistant's reauthentication prompt with a valid refresh token or new invitation.
- **HTTP 403:** Check that your pass allows remote activation. Brivo may require proximity or other checks for that access point.
- **Connection failure:** Check your internet connection. Before retrying, check whether the gate opened; Brivo may have received the first request.
- **Missing gate or door:** Reauthenticate to discover changes to your pass's access points.
- **Credentials could not be saved:** Check Home Assistant's disk space and storage permissions.
- **Different app client:** Use advanced setup with the credentials that issued your refresh token. The bundled profile is pinned to Mobile Pass 4.33.2 US production; Brivo may change or revoke it.
- **Missing icon in HACS:** Older HACS frontends do not display bundled integration icons. The icon is included in this integration; the upstream fix is in Home Assistant's 2026.11 Marketplace.

## Security

Shared app credentials identify the mobile application; they do not grant door access on their own. Your invitation, tokens, and pass credential remain private and are stored by Home Assistant. No APK or credential-hosting service is used during setup.

This is an unofficial integration, not affiliated with or endorsed by Brivo.

## Contributing

See [development and API details](docs/ARCHITECTURE.md) and [release instructions](docs/PUBLISHING.md). Report bugs through [GitHub Issues](https://github.com/ColeMundus/ha-brivo-mobile-pass/issues). Keep credentials out of reports; see [SECURITY.md](SECURITY.md).
