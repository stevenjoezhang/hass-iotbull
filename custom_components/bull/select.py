"""Select entities for Bull devices."""

from typing import TYPE_CHECKING, cast

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullSwitch
from .const import BULL_API_CLIENTS, CHARGER_PRODUCT_ID, DOMAIN
from .digital_display_socket import (
    DIGITAL_DISPLAY_SOCKET_PRODUCT_ID,
    master_power_is_on,
)

if TYPE_CHECKING:
    from .ble_charger import BullBleCharger

CHARGE_MODE_OPTIONS = {
    "plug_and_charge": 0,
    "automatic_start": 1,
}
CHARGE_MODE_BY_VALUE = {value: option for option, value in CHARGE_MODE_OPTIONS.items()}

DIGITAL_DISPLAY_SOCKET_SELECTS = {
    "ChargeMode": {
        "translation_key": "digital_display_socket_charge_mode",
        "options": {
            "smart": 0,
            "dual_laptop": 1,
            "sleep": 2,
            "balanced": 3,
        },
    },
    "OffScreenTime": {
        "translation_key": "digital_display_socket_off_screen_time",
        "options": {
            "30_seconds": 0,
            "1_minute": 1,
            "5_minutes": 2,
            "10_minutes": 3,
            "30_minutes": 4,
            "always_on": 5,
        },
    },
    "TimerType": {
        "translation_key": "digital_display_socket_clock_format",
        "options": {
            "12_hour": 0,
            "24_hour": 1,
        },
    },
}

DIGITAL_DISPLAY_SOCKET_CUSTOM_MODES = {
    4: "custom_1",
    5: "custom_2",
    6: "custom_3",
    7: "custom_4",
    8: "custom_5",
}


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


class BullDigitalDisplaySocketSelectEntity(SelectEntity):
    """Property-backed select for PID 296."""

    _attr_should_poll = False

    def __init__(
        self,
        device: BullSwitch,
        identifier: str,
        translation_key: str,
        options: dict[str, int],
    ) -> None:
        self._device = device
        self._identifier = identifier
        self._option_values = options
        self._options_by_value = {value: option for option, value in options.items()}
        self._attr_translation_key = translation_key
        device.register_entity(self, identifier, "PowerSwitch")

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
        return f"{self._device.iot_id}.{self._identifier}"

    @property
    def available(self) -> bool:
        return self._device.available and master_power_is_on(
            self._device.identifier_values
        )

    @property
    def options(self) -> list[str]:
        options = list(self._option_values)
        value = self._device.identifier_values.get(self._identifier)
        if self._identifier == "ChargeMode" and value in (
            DIGITAL_DISPLAY_SOCKET_CUSTOM_MODES
        ):
            options.append(DIGITAL_DISPLAY_SOCKET_CUSTOM_MODES[value])
        return options

    @property
    def current_option(self) -> str | None:
        value = self._device.identifier_values.get(self._identifier)
        if not isinstance(value, int):
            return None
        return self._options_by_value.get(
            value, DIGITAL_DISPLAY_SOCKET_CUSTOM_MODES.get(value)
        )

    async def async_select_option(self, option: str) -> None:
        if not master_power_is_on(self._device.identifier_values):
            raise HomeAssistantError("Device master power is off")

        if option not in self._option_values:
            if option == self.current_option:
                return
            raise HomeAssistantError(
                "Custom charging modes require a saved power profile"
            )
        await self._device.set_dp(self._identifier, self._option_values[option])


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up charging-mode selects."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities: list[SelectEntity] = [
        BullChargerModeEntity(device)
        for device in bull_api.device_list.values()
        if device.global_product_id in CHARGER_PRODUCT_ID and device.ble_charger
    ]
    for device in bull_api.device_list.values():
        if device.global_product_id not in DIGITAL_DISPLAY_SOCKET_PRODUCT_ID:
            continue
        entities.extend(
            BullDigitalDisplaySocketSelectEntity(
                device,
                identifier,
                config["translation_key"],
                config["options"],
            )
            for identifier, config in DIGITAL_DISPLAY_SOCKET_SELECTS.items()
        )
    async_add_entities(entities, update_before_add=False)
