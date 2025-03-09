"""Entity definition for switch devices."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_DEVICES, SWITCH_PRODUCT_ID, CHARGER_PRODUCT_ID
from .api import BullDevice

# https://developers.home-assistant.io/docs/core/entity/switch
class BullSwitchEntity(SwitchEntity):
    def __init__(self, device: BullDevice, identifier: str) -> None:
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
    def name(self) -> str:
        return self._device._identifier_names[self._identifier]

    @property
    def should_poll(self):
        """Return if platform should poll for updates."""
        return False

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return self._device._identifier_values[self._identifier]

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        await self._device.set_dp(self._identifier, 1)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        await self._device.set_dp(self._identifier, 0)


class BullChargerEntity(BullSwitchEntity):
    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return self._device._identifier_values["ChargerSwitch"]

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        await self._device.set_dp("ChargerSwitch", 1)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        await self._device.set_dp("ChargerSwitch", 0)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    entities = []
    for device in hass.data[DOMAIN][BULL_DEVICES].values():
        if device._global_product_id in SWITCH_PRODUCT_ID:
            for identifier in device._identifier_names:
                entities.append(BullSwitchEntity(device, identifier))
        elif device._global_product_id in CHARGER_PRODUCT_ID:
            for identifier in device._identifier_names:
                entities.append(BullChargerEntity(device, identifier))

    async_add_entities(entities, update_before_add=False)
