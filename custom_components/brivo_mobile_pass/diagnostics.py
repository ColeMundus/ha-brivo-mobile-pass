"""Allowlisted diagnostics: never serialize config, tokens, IDs, or locations."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    integration = await async_get_integration(hass, entry.domain)
    client = getattr(getattr(entry, "runtime_data", None), "client", None)
    telemetry = client.diagnostics if client is not None else {}
    return {
        "integration_version": integration.version,
        "loaded": client is not None,
        "access_point_count": len(
            client.access_points if client is not None else entry.data.get("access_points", [])
        ),
        "credential_present": bool(
            client.credential if client is not None else entry.data.get("credential")
        ),
        "last_successful_request_at": telemetry.get("last_successful_request_at"),
        "last_error": telemetry.get("last_error"),
        "last_error_at": telemetry.get("last_error_at"),
    }
