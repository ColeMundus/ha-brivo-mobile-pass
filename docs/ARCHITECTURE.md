# Development

## Structure

- `api.py`: Brivo HTTP requests, discovery, and token refresh.
- `config_flow.py`: Simplified invitation/token setup, advanced manual credentials, and reauthentication.
- `app_credentials.py`: Pinned shared US production application identity and source APK provenance; never personal tokens.
- `button.py`: One Open button per access point.
- `__init__.py`: Integration setup, token storage, and the connection-check action.
- `diagnostics.py`: Credential-free diagnostic information.

The integration uses Home Assistant's shared aiohttp session. Requests for each pass run one at a time to prevent concurrent token refreshes. Tokens are saved before an unlock request is sent. All requests run on demand.

Application credentials and discovered access points are stored in the config entry. Tokens are stored separately through Home Assistant's `Store` helper with owner-only file permissions. Setup saves credentials as soon as they are received, so a failed discovery step can resume without redeeming an invitation again.

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

Diagnostics contain the integration version, whether it is loaded, the access-point count, and whether a credential is present. Error messages omit sensitive response bodies and URLs.

## Checks

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests cover authentication, token persistence, invitation recovery, discovery, buttons, and the integration lifecycle. GitHub Actions also runs Hassfest and HACS validation.

For Home Assistant conventions, see the [integration developer guide](https://developers.home-assistant.io/docs/creating_component_index/), [config entries](https://developers.home-assistant.io/docs/config_entries_index/), and [config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/).
