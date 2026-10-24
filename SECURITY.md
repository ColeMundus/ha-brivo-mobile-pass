# Security

Keep invitations, personal access/refresh tokens, pass credentials, and manually supplied client secrets out of issues, screenshots, and commits. Home Assistant's configuration and backups contain credentials and should be kept private.

The integration intentionally bundles the shared US production OAuth application identity from Mobile Pass 4.33.2. It identifies the application, not a user, and does not authorize door access without a valid pass. Its presence is not a reason to publish personal authorization data. Setup contacts Brivo directly; there is no maintainer-operated token service.

Diagnostic downloads omit credentials and identifying information. Use them when reporting a bug.

Deleting the integration removes its token store. Failed or cancelled setup may leave a recovery record in `.storage/brivo_mobile_pass.setup.*`; remove it once it is no longer needed. Revoking a pass must be done through Brivo.

Report security vulnerabilities through [GitHub's private vulnerability reporting](https://github.com/ColeMundus/ha-brivo-mobile-pass/security/advisories/new).
