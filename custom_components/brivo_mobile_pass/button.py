"""One command button per authorized access point; no invented gate state."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BrivoAuthError, BrivoError
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        BrivoButton(entry, point) for point in entry.runtime_data.client.access_points
    )


class BrivoButton(ButtonEntity):
    """Request an unlock without implying that physical movement is confirmed."""

    _attr_has_entity_name = True
    _attr_translation_key = "open"
    _attr_icon = "mdi:gate"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, point: dict) -> None:
        self.entry = entry
        self.client = entry.runtime_data.client
        self.access_point_id = str(point["id"])
        self._attr_unique_id = f"{entry.entry_id}_{self.access_point_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=point["name"],
            manufacturer="Brivo",
            model="Mobile Pass access point",
        )

    async def async_press(self) -> None:
        try:
            await self.client.open_access_point(self.access_point_id)
        except BrivoAuthError as exc:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(exc)) from None
        except BrivoError as exc:
            raise HomeAssistantError(str(exc)) from None
