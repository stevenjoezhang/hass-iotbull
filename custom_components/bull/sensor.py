"""Entity definition for sensor devices."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_DEVICES, SWITCH_PRODUCT_ID, SENSOR_MAPPING, CHARGER_PRODUCT_ID
from .api import BullDevice

class BullSensorEntity(SensorEntity):
    def __init__(self, device: BullDevice, identifier: str):
        self._device = device
        self._identifier = identifier
        device._entities[identifier] = self

    @property
    def device_info(self):
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._device._iotId)
            },
            "name": self._device._official_product_name,
            "manufacturer": "Bull",
            "model": self._device._official_product_name,
            "suggested_area": self._device._room
        }

    @property
    def unique_id(self) -> str:
        return self._device._iotId + "." + self._identifier

    @property
    def name(self):
        # FIXME: may not work
        return f"{list(self._device._identifier_names.values())[0]}{SENSOR_MAPPING[self._identifier]['name']}"

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def state(self):
        value = self._device._identifier_values[self._identifier]
        if "scale" in SENSOR_MAPPING[self._identifier]:
            value /= SENSOR_MAPPING[self._identifier]["scale"]
        return value

    @property
    def unit_of_measurement(self):
        return SENSOR_MAPPING[self._identifier]["unit"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    entities = []
    for device in hass.data[DOMAIN][BULL_DEVICES].values():
        if device._global_product_id in SWITCH_PRODUCT_ID or device._global_product_id in CHARGER_PRODUCT_ID:
            for identifier in SENSOR_MAPPING:
                if identifier in device._identifier_values:
                    entities.append(BullSensorEntity(device, identifier))

    async_add_entities(entities, update_before_add=False)
