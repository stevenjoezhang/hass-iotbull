"""Number entities for Bull devices."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullSwitch
from .const import BULL_API_CLIENTS, DOMAIN
from .digital_display_socket import (
    DIGITAL_DISPLAY_SOCKET_PRODUCT_ID,
    brightness_value,
    master_power_is_on,
)


class BullDigitalDisplaySocketBrightnessEntity(NumberEntity):
    """PID 296 screen brightness percentage."""

    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, device: BullSwitch) -> None:
        self._device = device
        device.register_entity(self, "ScreenBrightValue", "PowerSwitch")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device.iot_id)},
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
        return f"{self._device.iot_id}.ScreenBrightValue"

    @property
    def name(self) -> str:
        return f"{self._device.nick_name}屏幕亮度"

    @property
    def available(self) -> bool:
        return self._device.available and master_power_is_on(
            self._device.identifier_values
        )

    @property
    def native_value(self) -> float | None:
        return brightness_value(self._device.identifier_values.get("ScreenBrightValue"))

    async def async_set_native_value(self, value: float) -> None:
        if not master_power_is_on(self._device.identifier_values):
            raise HomeAssistantError("Device master power is off")
        await self._device.set_dp("ScreenBrightValue", round(value))


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bull number entities."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = [
        BullDigitalDisplaySocketBrightnessEntity(device)
        for device in bull_api.device_list.values()
        if device.global_product_id in DIGITAL_DISPLAY_SOCKET_PRODUCT_ID
    ]
    async_add_entities(entities, update_before_add=False)
