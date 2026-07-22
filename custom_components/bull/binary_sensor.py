"""Entity definition for binary sensor devices."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullDevice
from .const import BULL_API_CLIENTS, DOMAIN, SENSOR_PRODUCT_ID


SENSOR_BINARY_ENTITIES = {
    61: (("GasStatus", "gas_status", BinarySensorDeviceClass.GAS),),
    63: (("WatervolumeStatus", "water_status", BinarySensorDeviceClass.MOISTURE),),
    66: (("HumanActivity", "motion", BinarySensorDeviceClass.MOTION),),
    68: (
        ("DoorStatus", "door", BinarySensorDeviceClass.DOOR),
        ("DismantleStatus", "tamper", BinarySensorDeviceClass.TAMPER),
    ),
}


def _app_binary_state(value) -> bool | None:
    """Interpret the 0/1 values handled by the official Sensor module."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        if value.strip() == "1":
            return True
        if value.strip() == "0":
            return False
    return None


class BullSensorBinarySensorEntity(BinarySensorEntity):
    """Representation of one persistent Sensor-module alarm property."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device: BullDevice,
        identifier: str,
        translation_key: str,
        device_class: BinarySensorDeviceClass,
    ) -> None:
        self._device = device
        self._identifier = identifier
        self._attr_translation_key = translation_key
        self._attr_device_class = device_class
        self._attr_unique_id = f"{device.iot_id}.{identifier}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.iot_id)},
            name=f"{device.room}{device.nick_name}",
            manufacturer="Bull",
            model=device.product_name,
            model_id=device.model_name,
            serial_number=device.iot_id,
            suggested_area=device.room,
            sw_version=device.firmware_version,
        )
        device.register_entity(self, identifier)

    @property
    def available(self) -> bool:
        """Return whether the sensor device is online."""
        return self._device.available

    @property
    def is_on(self) -> bool | None:
        """Return the App-defined alarm/activity/open state."""
        return _app_binary_state(self._device.identifier_values.get(self._identifier))


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
        return self._device.available


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
        if device.global_product_id not in SENSOR_PRODUCT_ID:
            continue
        for identifier, translation_key, device_class in SENSOR_BINARY_ENTITIES.get(
            device.global_product_id, ()
        ):
            if identifier not in device.identifier_values:
                continue
            entities.append(
                BullSensorBinarySensorEntity(
                    device,
                    identifier,
                    translation_key,
                    device_class,
                )
            )

    async_add_entities(entities, update_before_add=False)
