"""Entity definition for binary sensor devices."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullDevice
from .const import BULL_API_CLIENTS, DOMAIN


def _flatten_dict(data: dict, prefix: str = "") -> dict:
    flattened = {}
    for key, value in data.items():
        flat_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_dict(value, flat_key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                list_key = f"{flat_key}.{index}"
                if isinstance(item, dict):
                    flattened.update(_flatten_dict(item, list_key))
                else:
                    flattened[list_key] = item
        else:
            flattened[flat_key] = value
    return flattened


class BullConnectivityBinarySensorEntity(BinarySensorEntity):
    """Representation of a Bull IoT connectivity binary sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:devices"

    def __init__(self, device: BullDevice) -> None:
        self._device = device
        self._attr_should_poll = False
        self._attr_unique_id = f"{self._device.iot_id}.connectivity"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device.iot_id)},
            name=f"{self._device.room}{self._device.nick_name}",
            manufacturer="Bull",
            model=self._device.product_name,
            model_id=self._device.model_name,
            serial_number=self._device.iot_id,
            suggested_area=self._device.room,
            sw_version=self._device.firmware_version,
        )
        self._device._connectivity_entity = self

    @property
    def available(self) -> bool:
        """Return whether this entity is available."""
        return True

    @property
    def is_on(self) -> bool:
        """Return if connectivity is online."""
        raw_info = getattr(self._device, "raw_info", {})
        if not isinstance(raw_info, dict):
            return False
        return raw_info.get("status") in ("ONLINE", 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        attrs.update(_flatten_dict(getattr(self._device, "raw_info", {}), "info"))
        attrs.update(
            _flatten_dict(getattr(self._device, "raw_device_info", {}), "device_info")
        )
        attrs.update(
            _flatten_dict(getattr(self._device, "identifier_values", {}), "realtime")
        )
        return attrs


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT binary sensor platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []

    for device in bull_api.device_list.values():
        entities.append(BullConnectivityBinarySensorEntity(device))

    async_add_entities(entities, update_before_add=False)
