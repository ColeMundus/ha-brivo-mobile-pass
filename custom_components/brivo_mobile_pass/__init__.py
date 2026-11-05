"""Native Home Assistant access-point controls for Brivo Mobile Pass."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .api import BrivoAuthError, BrivoClient, BrivoError
from .const import DOMAIN, SIGNAL_ACCESS_POINTS_UPDATED, STORAGE_VERSION
from .storage import async_save_credentials

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_PENDING_TOKENS = f"{DOMAIN}_pending_tokens"


@dataclass
class RuntimeData:
    """State owned by one loaded config entry."""

    client: BrivoClient
    store: Store


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register a read-only connection check for scripts and diagnostics."""

    def select_entry(call: ServiceCall) -> ConfigEntry:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED and hasattr(entry, "runtime_data")
        ]
        if entry_id := call.data.get("config_entry_id"):
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if len(entries) != 1:
            raise HomeAssistantError("Select one loaded Brivo Mobile Pass integration")
        return entries[0]

    async def check_connection(call: ServiceCall) -> None:
        entry = select_entry(call)
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

    async def refresh_access_points(call: ServiceCall) -> None:
        entry = select_entry(call)
        runtime = entry.runtime_data
        client = runtime.client
        try:
            await client.refresh_access_points()
        except BrivoAuthError:
            if (
                entry.state is ConfigEntryState.LOADED
                and getattr(entry, "runtime_data", None) is runtime
            ):
                entry.async_start_reauth(hass)
            raise HomeAssistantError(
                "Brivo authentication failed; reauthenticate the integration"
            ) from None
        except BrivoError, OSError:
            raise HomeAssistantError(
                "Could not refresh Brivo access points; try again later"
            ) from None
        if (
            entry.state is not ConfigEntryState.LOADED
            or getattr(entry, "runtime_data", None) is not runtime
        ):
            raise HomeAssistantError("Select one loaded Brivo Mobile Pass integration")
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "credential": client.credential,
                "access_points": client.access_points,
            },
        )
        async_dispatcher_send(hass, f"{SIGNAL_ACCESS_POINTS_UPDATED}_{entry.entry_id}")

    hass.services.async_register(
        DOMAIN,
        "refresh_access_points",
        refresh_access_points,
        schema=vol.Schema({vol.Optional("config_entry_id"): str}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load durable tokens and buttons without polling or an external service."""
    store = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.tokens", private=True, atomic_writes=True
    )
    # Setup can rotate before a RuntimeData exists. Retain failed writes in RAM
    # across setup retries, just as the loaded client's dirty flag does.
    pending = hass.data.setdefault(_PENDING_TOKENS, {})
    stored = pending.get(entry.entry_id)
    if stored is not None:
        try:
            await async_save_credentials(store, stored)
        except BrivoError:
            raise ConfigEntryNotReady("Could not save Brivo credentials") from None
        pending.pop(entry.entry_id, None)
    else:
        stored = await store.async_load() or {}
    bootstrap = entry.data.get("tokens")
    bootstrap_id = stored.get("bootstrap_id")
    if bootstrap:
        incoming_id = hashlib.sha256(bootstrap["refresh_token"].encode()).hexdigest()
        # A delayed config-entry save must not replace an already rotated token.
        # A genuinely new import/reauthentication supersedes the previous cache.
        if incoming_id != bootstrap_id:
            stored = {"tokens": dict(bootstrap), "bootstrap_id": incoming_id}
            try:
                await async_save_credentials(store, stored)
            except BrivoError:
                raise ConfigEntryNotReady("Could not save Brivo credentials") from None
        bootstrap_id = incoming_id
    tokens = stored.get("tokens")
    if not tokens:
        raise ConfigEntryAuthFailed("Saved Brivo credentials are missing")

    async def persist(tokens: dict) -> None:
        payload = {"tokens": tokens, "bootstrap_id": bootstrap_id}
        pending[entry.entry_id] = payload
        await async_save_credentials(store, payload)
        pending.pop(entry.entry_id, None)

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
    """Drain credential writers before HA can set up a replacement runtime."""
    client = entry.runtime_data.client
    unloaded = False
    try:
        await client.async_close()
        unloaded = await hass.config_entries.async_unload_platforms(entry, [Platform.BUTTON])
        return unloaded
    except BrivoError:
        # Keep the in-memory rotation and platforms: a successor must not load
        # stale durable tokens if the final persistence attempt failed.
        return False
    finally:
        if not unloaded:
            client.reopen()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove locally stored credentials when the user deletes the integration."""
    hass.data.get(_PENDING_TOKENS, {}).pop(entry.entry_id, None)
    await Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.tokens", private=True
    ).async_remove()
