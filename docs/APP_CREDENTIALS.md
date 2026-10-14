# Get the app credentials

Download the Brivo Mobile Pass APK and run the extraction helper on your computer. It finds the US production client ID and secret for you.

## 1. Download the APK

Choose a plain **APK** for `com.brivo.pass` from one of these mirrors:

- [APKMirror — available versions](https://www.apkmirror.com/apk/brivo-systems-llc/brivo-mobile-pass/)
- [APKPure — download page](https://apkpure.net/brivo-mobile-pass/com.brivo.pass/download)
- [APKCombo — download page](https://apkcombo.com/brivo-mobile-pass/com.brivo.pass/)

Choose the newest version listed. Mirrors can have different versions available. APKM and XAPK bundles are not supported by the helper.

You only need the downloaded file. You do not need to install the app or connect a phone.

## 2. Run the helper

The helper needs Python 3.10 or later and [JADX](https://github.com/skylot/jadx). On macOS, install both with Homebrew:

```sh
brew install python jadx
```

On Linux, install Python 3 and JADX using your distribution's packages or the [JADX releases](https://github.com/skylot/jadx/releases).

[Download the helper](https://github.com/ColeMundus/ha-brivo-mobile-pass/releases/latest/download/extract_app_credentials.py) into the same folder as your APK. Open a terminal in that folder and run:

```sh
python3 extract_app_credentials.py "Brivo Mobile Pass.apk"
```

Replace the filename with the APK you downloaded. If you cloned this repository, run `python3 scripts/extract_app_credentials.py "/path/to/app.apk"` instead.

The helper saves `brivo-app-credentials.private.json`. Open it and copy `client_id` and `client_secret` into Home Assistant's setup form. The helper reads the APK locally and does not activate an invitation or contact Brivo.

If JADX is not on your PATH, pass its executable location:

```sh
python3 extract_app_credentials.py "app.apk" --jadx "/path/to/jadx/bin/jadx"
```

To save another copy, add `--output another-credentials.private.json`. Existing files are never overwritten.

## Compatibility

The helper was tested with Mobile Pass 4.33.2. It checks the US region and production build branches before saving the values. If a different app version changes that layout, it stops and points you to the manual method below.

For an existing refresh token, the app credentials must match the client that issued it. The helper extracts the regular US production pair; other clients need manual lookup.

## Manual lookup

Open the APK in `jadx-gui`. Search for `getClientId` and `getClientSecret` in `com.brivo.pass.Constants`. Read the `FIREBASE_UNITED_STATES` region and `RELEASE_PROD` build branches. If the decompiler uses numbered regions, check `WhenMappings`; version 4.33.2 maps the US region to `2`.

For an existing token, read its `client_id` locally and find the matching app constant. Do not submit the token to an online decoder:

```sh
python3 - <<'PY'
import base64
import getpass
import json

part = getpass.getpass("Refresh token: ").split(".")[1]
payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
print("Client ID:", payload["client_id"])
PY
```

If it matches `SH_CLIENT_ID`, use `SH_CLIENT_SECRET`. The app's selection logic is in `RefreshOnairAccessTokenUseCase`.

If those names have changed, search for `BrivoConfiguration.Builder` and follow the client ID and secret passed into it.

Keep the APK, extracted source, and credential file out of commits and issue reports.
