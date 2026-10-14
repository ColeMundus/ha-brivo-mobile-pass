# Find the Brivo app credentials

The OAuth client ID and client secret are included in the Brivo Mobile Pass Android app. They identify the application. Your invitation or refresh token supplies the authorization for your pass.

You can read the app credentials from your own copy of the APK with [JADX](https://github.com/skylot/jadx). Extract them before submitting an invitation in Home Assistant. This process reads the APK and does not activate a pass.

The class and method names below were verified in Mobile Pass 4.33.2. Other versions may rename or reorganize them. This integration currently uses Brivo's US endpoints.

## 1. Get the APK

If you already have the APK, continue to step 2.

Otherwise, install the official app on your Android phone. Install Google's [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools), enable USB debugging on the phone, and connect it to your computer.

In a macOS or Linux terminal:

```sh
umask 077
mkdir brivo-client.private
cd brivo-client.private
adb devices
adb -s DEVICE_SERIAL shell pm path com.brivo.pass
```

Replace `DEVICE_SERIAL` with your phone's serial from `adb devices`. Find the output line ending in `base.apk`, remove its `package:` prefix, and use that path to copy the APK:

```sh
adb -s DEVICE_SERIAL pull "/path/returned/by/pm/base.apk" brivo-pass.apk
```

Keep the APK and extracted source outside the integration repository.

## 2. Open it in JADX

Download JADX from its [releases page](https://github.com/skylot/jadx/releases) and follow its installation instructions. On macOS, you can also install it with Homebrew:

```sh
brew install jadx
jadx-gui brivo-pass.apk
```

Use JADX's text search to find `getClientId` and `getClientSecret`. In version 4.33.2, both methods are in:

```text
com.brivo.pass.Constants
```

## 3. Select the matching pair

For a new invitation, read the production-US branches of `getClientId` and `getClientSecret`:

- Region: `FIREBASE_UNITED_STATES`.
- Build: `RELEASE_PROD`.

The decompiler may represent regions as numbers. Check the `WhenMappings` class to resolve them. In version 4.33.2, the region switch maps `FIREBASE_UNITED_STATES` to `2`.

Copy the returned client ID and secret into Home Assistant's **Brivo app client ID** and **Brivo app client secret** fields. If a method returns another field or constructs a value, follow that assignment to its final string.

For an existing refresh token, the pair must match its `client_id` claim. You can read that claim locally without sending the token anywhere:

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

Match that client ID to the app's constants. If it matches `SH_CLIENT_ID`, use the corresponding `SH_CLIENT_SECRET`. The app's selection logic is in:

```text
com.brivo.pass.features.accesspoints.domain.usecases.RefreshOnairAccessTokenUseCase
```

Paste the client ID and secret as separate strings. The integration builds the HTTP authentication header itself.

## If the names have changed

Search for `BrivoConfiguration.Builder`, `refreshAccessToken`, or `oauth/token` and follow the client ID and secret used for those requests. In version 4.33.2, `BrivoSDKWrapper` passes them into `BrivoConfiguration.Builder`.

JADX may fail to decompile some methods. Its text search and recovered source can still help locate the credentials. See the [JADX troubleshooting guide](https://github.com/skylot/jadx/wiki/Troubleshooting-Q%26A) if the relevant code is unreadable.

## Keep the values local

Do not upload the APK, decompiled source, client secret, or personal tokens to the repository or an issue. The repository ignores `.private` directories and private files as a precaution.

Brivo can change its client credentials or API. Extracting the values locally does not guarantee that they will remain valid.
