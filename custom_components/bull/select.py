"""Select entities for Bull devices."""

from typing import TYPE_CHECKING, cast

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullSwitch
from .const import BULL_API_CLIENTS, CHARGER_PRODUCT_ID, DOMAIN

if TYPE_CHECKING:
    from .ble_charger import BullBleCharger

CHARGE_MODE_OPTIONS = {
    "plug_and_charge": 0,
    "automatic_start": 1,
}
CHARGE_MODE_BY_VALUE = {value: option for option, value in CHARGE_MODE_OPTIONS.items()}


class BullChargerModeEntity(SelectEntity):
    """PID 309 charging-mode control."""

    _attr_should_poll = False
    _attr_translation_key = "charge_mode"
    _attr_options = list(CHARGE_MODE_OPTIONS)

    def __init__(self, device: BullSwitch) -> None:
        self._device = device
        device.register_entity(self, "ChargeMode")

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
        return f"{self._device.iot_id}.ChargeMode"

    @property
    def available(self) -> bool:
        return self._device.available

    @property
    def current_option(self) -> str | None:
        value = self._device.identifier_values.get("ChargeMode")
        return CHARGE_MODE_BY_VALUE.get(value) if isinstance(value, int) else None

    async def async_select_option(self, option: str) -> None:
        charger = cast("BullBleCharger | None", self._device.ble_charger)
        if charger is None:
            raise RuntimeError("charger has no local BLE controller")
        await charger.async_set_charge_mode(CHARGE_MODE_OPTIONS[option])


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up charging-mode selects."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = [
        BullChargerModeEntity(device)
        for device in bull_api.device_list.values()
        if device.global_product_id in CHARGER_PRODUCT_ID and device.ble_charger
    ]
    async_add_entities(entities, update_before_add=False)
