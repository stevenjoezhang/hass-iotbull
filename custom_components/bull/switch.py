"""Entity definition for switch devices."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS, SWITCH_PRODUCT_ID, CHARGER_PRODUCT_ID
from .api import BullSwitch
from .digital_display_socket import (
    DIGITAL_DISPLAY_SOCKET_PORT_NAMES,
    DIGITAL_DISPLAY_SOCKET_PORTS,
    DIGITAL_DISPLAY_SOCKET_PRODUCT_ID,
    master_power_is_on,
    port_property_keys,
    port_property_value,
)


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
        if device.cloud_charger:
            device.register_entity(self, "DeviceState.WorkState")

    @property
    def name(self) -> str:
        return self._device.identifier_names.get(
            self._identifier, self._device.nick_name or self._device.product_name
        )

    @property
    def is_on(self) -> bool | None:
        """Check if Bull IoT switch is on."""
        if self._device.cloud_charger:
            return self._device.cloud_charger.is_charging
        return bool(self._device.identifier_values.get("ChargeSwitch", False))

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        if self._device.ble_charger:
            await self._device.ble_charger.async_set_charging(True)
        elif self._device.cloud_charger:
            await self._device.cloud_charger.async_set_charging(True)
        else:
            await self._device.set_dp("ChargeSwitch", 1)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        if self._device.ble_charger:
            await self._device.ble_charger.async_set_charging(False)
        elif self._device.cloud_charger:
            await self._device.cloud_charger.async_set_charging(False)
        else:
            await self._device.set_dp("ChargeSwitch", 0)


class BullDigitalDisplaySocketSwitchEntity(BullSwitchEntity):
    """Power control exposed by the PID 296 charging station."""

    def __init__(
        self,
        device: BullSwitch,
        identifier: str,
        name: str,
        *,
        port: str | None = None,
        guarded: bool = False,
    ) -> None:
        self._entity_name = name
        self._port = port
        self._guarded = guarded
        super().__init__(device, identifier)
        if port:
            _, dotted_identifier, parent_identifier = port_property_keys(
                port, "PowerSwitch"
            )
            device.register_entity(self, dotted_identifier, parent_identifier)
        if guarded:
            device.register_entity(self, "PowerSwitch")

    @property
    def name(self) -> str:
        return f"{self._device.nick_name}{self._entity_name}"

    @property
    def available(self) -> bool:
        """The official App disables subordinate controls with master power off."""
        return self._device.available and (
            not self._guarded or master_power_is_on(self._device.identifier_values)
        )

    @property
    def is_on(self) -> bool | None:
        if self._port:
            value = port_property_value(
                self._device.identifier_values, self._port, "PowerSwitch"
            )
        else:
            value = self._device.identifier_values.get(self._identifier)
        return bool(value) if value is not None else None

    def _ensure_control_available(self) -> None:
        if self._guarded and not master_power_is_on(self._device.identifier_values):
            raise HomeAssistantError("Device master power is off")

    async def async_turn_on(self, **kwargs):
        self._ensure_control_available()
        await super().async_turn_on(**kwargs)

    async def async_turn_off(self, **kwargs):
        self._ensure_control_available()
        await super().async_turn_off(**kwargs)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []
    for device in bull_api.device_list.values():
        if device.global_product_id in DIGITAL_DISPLAY_SOCKET_PRODUCT_ID:
            entities.append(
                BullDigitalDisplaySocketSwitchEntity(device, "PowerSwitch", "总电源")
            )
            entities.extend(
                BullDigitalDisplaySocketSwitchEntity(
                    device,
                    f"{port}PowerSwitch",
                    DIGITAL_DISPLAY_SOCKET_PORT_NAMES[port],
                    port=port,
                    guarded=True,
                )
                for port in DIGITAL_DISPLAY_SOCKET_PORTS
            )
            entities.append(
                BullDigitalDisplaySocketSwitchEntity(
                    device,
                    "OffScreenSwitch",
                    "自动熄屏",
                    guarded=True,
                )
            )
        elif device.global_product_id in SWITCH_PRODUCT_ID:
            for identifier in device.identifier_names:
                entities.append(BullSwitchEntity(device, identifier))
        elif device.global_product_id in CHARGER_PRODUCT_ID:
            if (
                "ChargeSwitch" in device.identifier_values
                or device.ble_charger
                or device.cloud_charger
            ):
                if device.cloud_charger:
                    # PID 193/195 have no ChargeSwitch property, but use the
                    # conceptual identifier for a stable HA entity unique ID.
                    identifier = "ChargeSwitch"
                else:
                    identifier = (
                        "ChargeSwitch"
                        if "ChargeSwitch" in device.identifier_names
                        else next(iter(device.identifier_names), "ChargeSwitch")
                    )
                entities.append(BullChargerEntity(device, identifier))

    async_add_entities(entities, update_before_add=False)
