"""Entity definition for switch devices."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS, SWITCH_PRODUCT_ID, CHARGER_PRODUCT_ID
from .api import BullSwitch


# https://developers.home-assistant.io/docs/core/entity/switch
class BullSwitchEntity(SwitchEntity):
    """Representation of a Bull IoT switch."""

    def __init__(self, device: BullSwitch, identifier: str) -> None:
        self._device = device
        self._identifier = identifier
        self._attr_should_poll = False
        device.register_entity(self, identifier)

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
        return self._device.iot_id + "." + self._identifier

    @property
    def name(self) -> str:
        return self._device.identifier_names[self._identifier]

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return self._device.identifier_values[self._identifier]

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        await self._device.set_dp(self._identifier, 1)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        await self._device.set_dp(self._identifier, 0)


class BullChargerEntity(BullSwitchEntity):
    """Representation of a Bull IoT charger switch."""

    _attr_translation_key = "charger"

    def __init__(self, device: BullSwitch, identifier: str) -> None:
        super().__init__(device, identifier)
        device.register_entity(self, "ChargeSwitch")

    @property
    def name(self) -> str:
        return self._device.identifier_names.get(
            self._identifier, self._device.nick_name or self._device.product_name
        )

    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return bool(self._device.identifier_values.get("ChargeSwitch", False))

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        if self._device.ble_charger:
            await self._device.ble_charger.async_set_charging(True)
        else:
            await self._device.set_dp("ChargeSwitch", 1)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        if self._device.ble_charger:
            await self._device.ble_charger.async_set_charging(False)
        else:
            await self._device.set_dp("ChargeSwitch", 0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []
    for device in bull_api.device_list.values():
        if device.global_product_id in SWITCH_PRODUCT_ID:
            for identifier in device.identifier_names:
                entities.append(BullSwitchEntity(device, identifier))
        elif device.global_product_id in CHARGER_PRODUCT_ID:
            if "ChargeSwitch" in device.identifier_values or device.ble_charger:
                identifier = (
                    "ChargeSwitch"
                    if "ChargeSwitch" in device.identifier_names
                    else next(iter(device.identifier_names), "ChargeSwitch")
                )
                entities.append(BullChargerEntity(device, identifier))

    async_add_entities(entities, update_before_add=False)
