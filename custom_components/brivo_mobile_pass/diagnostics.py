"""Allowlisted diagnostics: never serialize config, tokens, IDs, or locations."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    integration = await async_get_integration(hass, entry.domain)
    return {
        "integration_version": integration.version,
        "loaded": hasattr(entry, "runtime_data"),
        "access_point_count": len(entry.data.get("access_points", [])),
        "credential_present": bool(entry.data.get("credential")),
    }
