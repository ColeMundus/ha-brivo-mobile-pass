# Credentials and security

Do not report issues containing invitations, client secrets, tokens, email addresses, pass/device/site IDs, locations, HA `.storage`, or complete HA backups. Diagnostics are deliberately limited to counts and booleans. Example/test values are fabricated.

Real credentials belong in Home Assistant's local storage, never in this repository. Gitignore is a second line of defense; review tracked files before pushing. Home Assistant configuration and backups contain usable credentials and must be protected. Deleting an integration removes its live token store but does not revoke a credential at Brivo. Cancelled/failed setup can retain a recovery record in `.storage/brivo_mobile_pass.setup.*`; remove it locally only when recovery is no longer needed.

This integration makes commands to physical access-control devices using the user's granted pass. It does not bypass Brivo permission checks or report physical open/closed state. A successful response establishes server acceptance only.

Report security problems privately using GitHub's private vulnerability reporting when enabled by the maintainer. Do not include secrets in public issues.
