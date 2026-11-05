# Development

## Structure

- `api.py`: Brivo HTTP requests, discovery, and token refresh.
- `config_flow.py`: Simplified invitation/token setup, advanced manual credentials, and reauthentication.
- `app_credentials.py`: Pinned shared US production application identity and source APK provenance; never personal tokens.
- `button.py`: One Open button per access point.
- `__init__.py`: Integration setup, token storage, and on-demand connection/discovery actions.
- `diagnostics.py`: Credential-free diagnostic information.

The integration uses Home Assistant's shared aiohttp session. Requests for each pass run one at a time to prevent concurrent token refreshes. Tokens are saved before an unlock request is sent. All requests run on demand.

Application credentials and discovered access points are stored in the config entry. Tokens are stored separately through Home Assistant's `Store` helper with owner-only file permissions. Setup saves credentials as soon as they are received, so a failed discovery step can resume without redeeming an invitation again.

Credential stores use atomic writes and verify the actual saved JSON file in an
executor before reporting success. Home Assistant's Store can log disk failures
without raising; its return value or in-memory cache alone is not proof of a save.
Runtime and setup flows retain pending rotations in memory while storage fails.
That recovery cannot survive process loss until the credentials reach disk.

Unloading first retires the client: queued operations are rejected before HTTP,
while the current lock holder is allowed to finish. Unload then saves any pending
rotated tokens before removing platforms. Home Assistant serializes unload/setup,
so a replacement loads the final durable tokens; a new reauthentication bootstrap
still supersedes that previous token lineage. The retired client cannot issue
further requests or overwrite its replacement's token Store.

If draining, persistence, or platform unload fails or is cancelled, the same
client is reopened and retains any unsaved rotation for a later persistence
attempt; a replacement must not start with stale tokens. A failed unload returns
`False`, which Home Assistant marks `FAILED_UNLOAD` (requiring a restart, not an
automatic reload retry). If token storage is still failing, do not restart and
expect unsaved in-memory tokens to survive; repair storage and recover/save those
credentials first. Cancelling the unload wait does not cancel the active request.
No background polling or additional HTTP retries are introduced.

## Application identity

New standard setups copy the bundled Mobile Pass 4.33.2 US production client identity into the config entry. Upgrades do not replace stored app credentials. Standard reauthentication reuses the entry's identity. Advanced setup accepts explicit client credentials for compatible clients using the same US endpoints.

The token's locally decoded `client_id`, when available, is only a mismatch hint. Brivo still validates authorization; decoding a JWT does not verify its signature. No token is sent to an online decoder.

Do not automatically replace client profiles on refresh failures: refresh tokens are bound to their issuing client. Credentials and one-shot invitation recovery must remain paired. The integration does not download APKs, run decompilers, or contact a credential-hosting service.

## API

Authentication uses `https://auth.brivo.com`. Access-point requests use `https://pi.brivo.com`.

| Operation | Request | Authentication |
| --- | --- | --- |
| Activate invitation | POST `/oauth/token`; form fields `grant_type=invitation`, `email`, `access_code` | Basic: OAuth client ID and secret |
| Refresh token | POST `/oauth/token`; query parameters `grant_type=refresh_token`, `refresh_token` | Basic: OAuth client ID and secret |
| Read pass credential | GET `/api/pass` | Bearer access token |
| List sites | GET `/api/sites?offset=…&filter=` | Bearer access token |
| List access points | GET `/api/sites/{site_id}/access-points?offset=…&filter=` | Bearer access token |
| Unlock | POST `/api/access-points/{id}/unlock` | Bearer access token |

Unlock requests contain the pass credential and an empty Wi-Fi proximity list:

```json
{"credentialValue": "<pass credential>", "proximity": {"wifi": []}}
```

The client refreshes before expiry. On HTTP 401, it refreshes and retries once. It does not retry network failures or other HTTP errors because an unlock may already have reached Brivo.

Discovery refresh is separate from reauthentication. It validates the saved pass,
collects the complete access-point snapshot, and applies it only after every page
succeeds. An empty refresh result is valid: previously discovered points become
unavailable. A failed refresh preserves the previous snapshot. Existing button
unique IDs remain tied to the config entry and access-point ID; refresh must not
recreate user customizations or enable user-disabled entities.

Diagnostics contain the integration version, whether it is loaded, the access-point
count, whether a credential is present, and allowlisted in-memory health metadata.
No additional polling is used. Error messages omit sensitive response bodies and
URLs; diagnostics never serialize config entries, tokens, or discovered identifiers.
`last_successful_request_at` and `last_error_at` are UTC Unix seconds, or `null`
until known. Request success includes token-refresh responses that decode
successfully; later schema validation or storage can still fail. `last_error` is a
fixed category: `auth`, `connection`, `timeout`, `invalid_response`,
`credentials_persistence`, `http`, `no_access_points`, or
`access_point_unavailable`. Only a complete successful operation clears an error;
an intermediate successful request does not hide a failed operation. These fields
reset on reload/restart and are not a physical access-point state.

## Checks

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The default `dev` group pins Core **2026.10.0** and its matching legacy custom
component harness. The `ci-latest` group pins Core **2026.11.0** and the official
`pytest-homeassistant` harness. The groups conflict intentionally; do not override
one harness's Core dependency with another version. Reproduce both locked lanes
in separate environments:

```sh
for group in dev ci-latest; do
  UV_PROJECT_ENVIRONMENT="${TMPDIR:?}/brivo-ci-$group" \
    uv sync --locked --no-default-groups --group "$group"
  UV_PROJECT_ENVIRONMENT="$TMPDIR/brivo-ci-$group" uv run --no-sync pytest
done
```

When advancing the latest stable lane, update its Core/harness pins together and
run `uv lock`. CI verifies the installed distribution versions explicitly.

Tests cover authentication, token persistence, invitation recovery, discovery,
button reconciliation, diagnostic privacy, and the integration lifecycle. GitHub
Actions also runs a minimum/latest Home Assistant compatibility matrix, Actionlint,
Hassfest, and HACS validation. All compatibility lanes must succeed before the
required `tests` gate passes. See [safe live checks](TESTING.md) for the separate
installed-integration acceptance test; mocked provider calls do not establish live
Brivo authorization.

For Home Assistant conventions, see the [integration developer guide](https://developers.home-assistant.io/docs/creating_component_index/), [config entries](https://developers.home-assistant.io/docs/config_entries_index/), and [config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/).
