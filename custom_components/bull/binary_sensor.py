"""Entity definition for binary sensor devices."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS
from .api import BullDevice


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

    def __init__(self, device: BullDevice) -> None:
        self._device = device
        self._attr_should_poll = False
        self._device._connectivity_entity = self

    @property
    def device_info(self):
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._device.iot_id)
            },
            "name": f"{self._device.room}{self._device.nick_name}",
            "manufacturer": "Bull",
            "model": self._device.product_name,
            "model_id": self._device.model_name,
            "serial_number": self._device.iot_id,
            "suggested_area": self._device.room,
            "sw_version": self._device.firmware_version,
        }

    @property
    def unique_id(self) -> str:
        return self._device.iot_id + ".connectivity"

    @property
    def name(self) -> str:
        return f"连通性"

    @property
    def device_class(self):
        return BinarySensorDeviceClass.CONNECTIVITY

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def is_on(self) -> bool:
        """Return if the device is connected."""
        return self._device.available

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        attrs = {}
        attrs.update(_flatten_dict(getattr(self._device, "raw_info", {}), "info"))
        attrs.update(_flatten_dict(getattr(self._device, "raw_device_info", {}), "device_info"))
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
