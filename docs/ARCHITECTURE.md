# Architecture and observed API

The integration runs inside HA Core. `api.py` handles HTTP, serialization, and token persistence; `config_flow.py` handles UI setup and recovery; `button.py` exposes commands; `__init__.py` owns config-entry lifecycle and durable token storage. A per-pass lock prevents concurrent refreshes. HA owns its shared aiohttp session, which the integration never closes.

Config entries hold application credentials, the pass moniker, and discovered access points. Current access/refresh tokens live in a dedicated HA Store per entry. Bootstrap tokens are removed from entry data after the store is written. A temporary setup Store prevents lost refresh-token rotation on failed/cancelled setup and guards one-time invitation redemption. No periodic job, external process, or HA long-lived API token is needed at runtime.

| Operation | Observed request | Authentication |
| --- | --- | --- |
| Invitation | POST `https://auth.brivo.com/oauth/token`; form `grant_type=invitation`, `email`, `access_code` | Basic OAuth application credentials |
| Refresh | POST same endpoint; query `grant_type=refresh_token`, `refresh_token` | Basic matching application credentials |
| Credential | GET `https://pi.brivo.com/api/pass` | Bearer access token |
| Sites | GET `/api/sites?offset=…&filter=` | Bearer access token |
| Access points | GET `/api/sites/{site_id}/access-points?offset=…&filter=` | Bearer access token |
| Unlock | POST `/api/access-points/{id}/unlock` | Bearer access token |

Unlock JSON is `{"credentialValue":"<pass moniker>","proximity":{"wifi":[]}}`. The refresh JWT can identify its client ID; a private migration may supply matching additional client secrets. Public UI setup accepts one matching client ID/secret pair. URLs with refresh-token query parameters are never included in errors or logs.

The API client refreshes before expiry and retries once on an explicit HTTP 401 after refresh. It never retries timeouts, connection failures, server errors, or ambiguous unlock results. HTTP 403 is a permission/policy failure, not automatic revocation of the entire account. Diagnostic output is an allowlist of counts and booleans; it contains no tokens, application credentials, pass IDs, device IDs, names, emails, or locations.

This protocol was derived from APK inspection with REA/JADX and verified with an authorized account. It was not derived from a TLS packet capture. Static evidence, invitation redemption, refresh, discovery, and server-accepted unlocks establish the implemented flow. Firmware, BLE/NFC, private Firebase login, two-factor policies, and physical state are not implemented.

## Design references

- [Home Assistant developer documentation](https://developers.home-assistant.io/docs/creating_component_index/): native config entries, async HTTP, setup retries, and entity lifecycle.
- [Config entries](https://developers.home-assistant.io/docs/config_entries_index/) and [config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/).
- [Custom integration translations](https://developers.home-assistant.io/docs/internationalization/custom_integration/).
- [Integration quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/): used as engineering guidance, not a certification claim.
- [Community development discussion](https://community.home-assistant.io/t/what-are-good-practices-when-developing-new-integrations/851083): keep protocol code separate from HA entities and setup.
- [Community integration examples discussion](https://community.home-assistant.io/t/working-example-of-ha-integration/730465): config flow and testing examples; official docs take precedence.
- [Home Assistant wiki index](https://community.home-assistant.io/c/community-guides/51): community guides supplement the official developer documentation.
