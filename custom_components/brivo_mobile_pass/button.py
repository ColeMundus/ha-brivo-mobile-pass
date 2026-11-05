"""One command button per authorized access point; no invented gate state."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BrivoAuthError, BrivoError
from .const import DOMAIN, SIGNAL_ACCESS_POINTS_UPDATED


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: dict[str, BrivoButton] = {}

    @callback
    def reconcile() -> None:
        added = []
        for point in entry.runtime_data.client.access_points:
            point_id = str(point["id"])
            if point_id not in entities:
                entities[point_id] = BrivoButton(entry, point)
                added.append(entities[point_id])
            else:
                registry = er.async_get(hass)
                entity_id = registry.async_get_entity_id(
                    "button", DOMAIN, f"{entry.entry_id}_{point_id}"
                )
                registered = registry.async_get(entity_id) if entity_id else None
                device = (
                    dr.async_get(hass).async_get(registered.device_id)
                    if registered and registered.device_id
                    else None
                )
                if device and device.name != point["name"]:
                    dr.async_get(hass).async_update_device(device.id, name=point["name"])
        for entity in entities.values():
            if entity.hass is not None and entity.entity_id is not None:
                entity.async_write_ha_state()
        if added:
            async_add_entities(added)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, f"{SIGNAL_ACCESS_POINTS_UPDATED}_{entry.entry_id}", reconcile
        )
    )
    reconcile()


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

    @property
    def available(self) -> bool:
        """Removed or disabled provider targets cannot be used."""
        return any(str(point["id"]) == self.access_point_id for point in self.client.access_points)

    async def async_press(self) -> None:
        try:
            await self.client.open_access_point(self.access_point_id)
        except BrivoAuthError as exc:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(exc)) from None
        except BrivoError as exc:
            raise HomeAssistantError(str(exc)) from None
