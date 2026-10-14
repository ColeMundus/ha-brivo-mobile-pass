"""Native Home Assistant access-point controls for Brivo Mobile Pass."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import BrivoAuthError, BrivoClient, BrivoError
from .const import DOMAIN, STORAGE_VERSION

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class RuntimeData:
    """State owned by one loaded config entry."""

    client: BrivoClient
    store: Store


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register a read-only connection check for scripts and diagnostics."""

    async def check_connection(call: ServiceCall) -> None:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if hasattr(entry, "runtime_data")
        ]
        if entry_id := call.data.get("config_entry_id"):
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if len(entries) != 1:
            raise HomeAssistantError("Select one loaded Brivo Mobile Pass integration")
        entry = entries[0]
        try:
            await entry.runtime_data.client.check_connection()
        except BrivoAuthError as exc:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(str(exc)) from None
        except BrivoError as exc:
            raise HomeAssistantError(str(exc)) from None

    hass.services.async_register(
        DOMAIN,
        "check_connection",
        check_connection,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load durable tokens and buttons without polling or an external service."""
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.tokens", private=True)
    stored = await store.async_load() or {}
    bootstrap = entry.data.get("tokens")
    bootstrap_id = stored.get("bootstrap_id")
    if bootstrap:
        incoming_id = hashlib.sha256(bootstrap["refresh_token"].encode()).hexdigest()
        # A delayed config-entry save must not replace an already rotated token.
        # A genuinely new import/reauthentication supersedes the previous cache.
        if incoming_id != bootstrap_id:
            stored = {"tokens": dict(bootstrap), "bootstrap_id": incoming_id}
            await store.async_save(stored)
        bootstrap_id = incoming_id
    tokens = stored.get("tokens")
    if not tokens:
        raise ConfigEntryAuthFailed("Saved Brivo credentials are missing")

    async def persist(tokens: dict) -> None:
        await store.async_save({"tokens": tokens, "bootstrap_id": bootstrap_id})

    client = BrivoClient(
        async_get_clientsession(hass),
        dict(entry.data["application"]),
        entry.data["credential"],
        list(entry.data["access_points"]),
        tokens,
        persist,
    )
    try:
        await client.check_connection()
    except BrivoAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from None
    except BrivoError as exc:
        raise ConfigEntryNotReady(str(exc)) from None
    entry.runtime_data = RuntimeData(client, store)
    if bootstrap:
        data = dict(entry.data)
        data.pop("tokens", None)
        staging_key = data.pop("staging_key", None)
        hass.config_entries.async_update_entry(entry, data=data)
        if staging_key:
            await Store(hass, STORAGE_VERSION, staging_key, private=True).async_remove()
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.BUTTON])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all entities; there are no background tasks to stop."""
    return await hass.config_entries.async_unload_platforms(entry, [Platform.BUTTON])


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove locally stored credentials when the user deletes the integration."""
    await Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.tokens", private=True
    ).async_remove()
