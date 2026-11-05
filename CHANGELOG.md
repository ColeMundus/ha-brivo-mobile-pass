# Changelog

## 0.3.0

- Added an on-demand access-point refresh action without reauthentication, invitation redemption, or unlock requests.
- Preserved button identities and user customizations when access points are added, renamed, removed, or restored.
- Added credential-free in-memory request-health diagnostics and privacy regression tests.
- Drained active requests and persisted rotated tokens before reload so retired clients cannot overwrite replacement credentials.
- Verified atomic credential writes against the saved file, including disk failures that Home Assistant logs without raising.
- Added minimum/latest Home Assistant compatibility checks and Actionlint to CI.
- Added guarded automation for routine Dependabot updates.

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
