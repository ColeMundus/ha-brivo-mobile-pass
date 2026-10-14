"""Allowlisted diagnostics: never serialize config, tokens, IDs, or locations."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    return {
        "integration_version": "0.1.0",
        "loaded": hasattr(entry, "runtime_data"),
        "access_point_count": len(entry.data.get("access_points", [])),
        "credential_present": bool(entry.data.get("credential")),
    }
