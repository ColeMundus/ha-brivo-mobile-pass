# Changelog

## 0.2.0

- Bundled the US production Mobile Pass app credentials: new invitation setup only needs an email and unused invitation code.
- Simplified existing-pass setup to a refresh token, with advanced manual credentials for other app clients.
- Preserved saved client credentials on upgrades, reauthentication, and setup recovery.
- Fixed the README image URL for HACS and updated installation instructions for the default catalog.
- Removed the obsolete APK extractor, its tests, extraction guide, and helper-specific lint configuration.

## 0.1.1

- Added a local APK helper to extract the US production app credentials.
- Simplified setup instructions with APK download links and a manual fallback.
- Added README badges, an Open in HACS link, and issue templates.

## 0.1.0

- Added Open buttons for authorized Brivo Mobile Pass access points.
- Added invitation and refresh-token setup, token storage, and reauthentication.
- Added a connection-check action and diagnostics.
