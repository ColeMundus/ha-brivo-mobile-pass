# Security

Keep client secrets, invitations, and tokens out of issues, screenshots, and commits. Home Assistant's configuration and backups contain credentials and should be kept private.

Diagnostic downloads omit credentials and identifying information. Use them when reporting a bug.

Deleting the integration removes its token store. Failed or cancelled setup may leave a recovery record in `.storage/brivo_mobile_pass.setup.*`; remove it once it is no longer needed. Revoking a pass must be done through Brivo.

Report security vulnerabilities through [GitHub's private vulnerability reporting](https://github.com/ColeMundus/ha-brivo-mobile-pass/security/advisories/new).
