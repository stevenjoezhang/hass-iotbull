"""Entity definition for charger devices."""

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_DEVICES, CHARGER_PRODUCT_ID
from .switch import BullSwitchEntity

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
        if device._global_product_id in CHARGER_PRODUCT_ID:
            for identifier in device._identifier_names:
                entities.append(BullChargerEntity(device, identifier))

    async_add_entities(entities, update_before_add=False)
