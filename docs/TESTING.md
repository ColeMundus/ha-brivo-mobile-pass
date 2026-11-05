# Safe installed-integration checks

Offline tests mock Brivo requests. Passing CI does not establish that a real pass
is authorized, that a HACS update loaded correctly, or that a physical gate moved.
Keep those claims separate.

## Before an upgrade

1. Confirm HACS reports the expected installed release and the Brivo integration
   is loaded. Record the Home Assistant version and the number of registered,
   available, and user-disabled access-point buttons.
2. Run `brivo_mobile_pass.check_connection`, selecting the integration if more
   than one pass is configured. It validates the saved pass without unlocking.
3. Download integration diagnostics. Do not copy Home Assistant storage files,
   tokens, invitations, or account identifiers into issues or test fixtures.
4. Keep a Home Assistant backup and record the previous release for rollback.
   Installing an update or restarting Home Assistant requires the owner's approval.

## Upgrade through HACS

1. Install the reviewed release through HACS, rather than copying development
   files over the installed integration. Do not upgrade Home Assistant itself as
   part of this check.
2. Restart Home Assistant when prompted. Merely reloading a config entry does not
   guarantee that changed Python modules have been reimported.
3. Confirm HACS's installed version, the version in downloaded diagnostics, and
   the integration's loaded state agree. Confirm the saved pass works without
   providing new app credentials or redeeming another invitation.
4. Run `brivo_mobile_pass.check_connection` again.
5. Run `brivo_mobile_pass.refresh_access_points`. Confirm discovery completes,
   existing button entity/unique IDs and disabled settings are preserved, and
   discovered points remain usable. This action must never request an unlock.
6. Reload the integration and repeat the connection check. Confirm the refreshed
   access-point snapshot and entity identities survive. Inspect diagnostics for
   safe health metadata and absence of credentials, names, IDs, and locations.

Do not deliberately revoke or edit a real pass just to test failure paths. The
mocked HA harness covers malformed responses, authentication/network failures,
concurrency, removed/reappearing points, and diagnostic privacy.

## Consequential checks are separate

A fresh invitation setup consumes a one-use invitation. Only test it with explicit
authorization and an invitation intended for that purpose. Do not redeem one as
routine release verification.

A button press requests physical access. Only test a specific door/gate with
explicit authorization and someone able to observe it safely. Never retry an
uncertain unlock automatically. A successful HTTP response is not evidence that a
gate physically opened. Neither check is required for the non-actuating smoke test.

## Record results privately

Record the release/commit, Home Assistant version, installation path, each check's
actual outcome, entity counts/identity preservation, and any skipped checks.
Keep account-specific evidence out of the public repository. Report offline CI,
live connection/discovery, restart recovery, fresh setup, and physical operation
as distinct verification results.
