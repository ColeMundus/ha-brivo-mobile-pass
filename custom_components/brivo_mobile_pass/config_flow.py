"""UI setup, one-shot invitations, import, and reauthentication."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.storage import Store

from .api import BrivoAuthError, BrivoClient, BrivoError
from .app_credentials import DEFAULT_APPLICATION
from .const import DOMAIN, NAME, STORAGE_VERSION


def secret() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def token_client_hint(token: str) -> str | None:
    """Read an unverified routing hint, never proof of authorization."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError, UnicodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("client_id"), str):
        return payload["client_id"]
    return None


class BrivoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Keep consumed invitations and rotated tokens recoverable on cancellation."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> dict:
        return self.async_show_menu(
            step_id="user", menu_options=["invitation", "token", "advanced"]
        )

    async def async_step_reauth(self, entry_data: dict) -> dict:
        return await self.async_step_user()

    async def async_step_token(self, user_input: dict | None = None) -> dict:
        return await self._credentials("token", user_input)

    async def async_step_invitation(self, user_input: dict | None = None) -> dict:
        return await self._credentials("invitation", user_input)

    async def async_step_advanced(self, user_input: dict | None = None) -> dict:
        return self.async_show_menu(
            step_id="advanced", menu_options=["manual_invitation", "manual_token"]
        )

    async def async_step_manual_token(self, user_input: dict | None = None) -> dict:
        return await self._credentials("token", user_input, manual=True)

    async def async_step_manual_invitation(self, user_input: dict | None = None) -> dict:
        return await self._credentials("invitation", user_input, manual=True)

    async def _credentials(
        self, mode: str, user_input: dict | None, *, manual: bool = False
    ) -> dict:
        errors = {}
        if user_input:
            if manual:
                application = {k: user_input[k] for k in ("client_id", "client_secret")}
            elif self.source == config_entries.SOURCE_REAUTH:
                application = dict(self._get_reauth_entry().data["application"])
            else:
                application = dict(DEFAULT_APPLICATION)
            if mode == "token" and not manual:
                hint = token_client_hint(user_input["refresh_token"])
                if hint is not None and hint != application["client_id"]:
                    return self._credentials_form(mode, manual, {"base": "unsupported_client"})
            fingerprint = hashlib.sha256(
                f"{application['client_id']}:{user_input.get('email', '')}:{user_input.get('invitation_code', user_input.get('refresh_token', ''))}".encode()
            ).hexdigest()
            key = f"{DOMAIN}.setup.{fingerprint}"
            store = Store(self.hass, STORAGE_VERSION, key, private=True)
            staged = await store.async_load()
            if staged and not staged.get("tokens", {}).get("refresh_token"):
                return self.async_abort(reason="invitation_uncertain")
            if staged is None:
                staged = {
                    "application": application,
                    "tokens": {"refresh_token": user_input["refresh_token"]}
                    if mode == "token"
                    else {},
                }
                # Record before redemption. A crash cannot silently resend the code.
                await store.async_save(staged)
            elif manual:
                # Explicit same-client secret corrections are allowed. Normal
                # retries must retain the app paired with the saved tokens.
                staged["application"] = application
                await store.async_save(staged)

            async def persist(tokens: dict[str, Any]) -> None:
                staged["tokens"] = tokens
                await store.async_save(staged)

            client = BrivoClient(
                async_get_clientsession(self.hass),
                staged["application"],
                "",
                [],
                staged["tokens"],
                persist,
            )
            try:
                if mode == "invitation" and not client.tokens.get("refresh_token"):
                    await client.redeem_invitation(
                        user_input["email"], user_input["invitation_code"]
                    )
                await client.discover()
                data = {
                    "application": staged["application"],
                    "credential": client.credential,
                    "access_points": client.access_points,
                    "tokens": client.tokens,
                    "staging_key": key,
                }
                return await self._finish(data)
            except BrivoAuthError:
                errors["base"] = "invalid_auth"
            except BrivoError:
                errors["base"] = "cannot_connect"
        return self._credentials_form(mode, manual, errors)

    def _credentials_form(self, mode: str, manual: bool, errors: dict) -> dict:
        schema = {}
        if manual:
            schema = {
                vol.Required("client_id"): str,
                vol.Required("client_secret"): secret(),
            }
        if mode == "token":
            schema[vol.Required("refresh_token")] = secret()
        else:
            schema[vol.Required("email")] = TextSelector(
                TextSelectorConfig(type=TextSelectorType.EMAIL)
            )
            schema[vol.Required("invitation_code")] = secret()
        return self.async_show_form(
            step_id=f"manual_{mode}" if manual else mode,
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict) -> dict:
        """Import already-authenticated private credentials without redemption."""
        if not all(
            user_input.get(k) for k in ("application", "credential", "access_points", "tokens")
        ):
            return self.async_abort(reason="invalid_import")
        return await self._finish(user_input)

    async def _finish(self, data: dict) -> dict:
        unique_id = hashlib.sha256(data["credential"].encode()).hexdigest()
        await self.async_set_unique_id(unique_id)
        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if any(
                e.unique_id == unique_id and e.entry_id != entry.entry_id
                for e in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            return self.async_update_reload_and_abort(entry, data=data, unique_id=unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=NAME, data=data)
