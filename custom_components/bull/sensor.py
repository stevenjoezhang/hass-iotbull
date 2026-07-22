"""Entity definition for sensor devices."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor.const import SensorStateClass

from .const import (
    DOMAIN,
    BULL_API_CLIENTS,
    SWITCH_PRODUCT_ID,
    SENSOR_MAPPING,
    CHARGER_PRODUCT_ID,
)
from .api import BullSwitch


def _is_sensor_meta(value) -> bool:
    return isinstance(value, dict) and {"name", "unit", "class"}.issubset(value.keys())


def _iter_sensor_specs(mapping: dict):
    """Yield normalized sensor specs as (entity_identifier, value_key, meta)."""
    for identifier, config in mapping.items():
        if _is_sensor_meta(config):
            yield identifier, identifier, config
            continue

        if isinstance(config, dict):
            for child_identifier, child_config in config.items():
                if _is_sensor_meta(child_config):
                    key = f"{identifier}.{child_identifier}"
                    yield key, key, child_config


class BullSensorEntity(SensorEntity):
    """Representation of a Bull IoT sensor."""

    def __init__(
        self,
        device: BullSwitch,
        entity_identifier: str,
        value_key: str,
        meta: dict,
    ):
        self._device = device
        self._entity_identifier = entity_identifier
        self._value_key = value_key
        self._meta = meta
        self._attr_device_class = self._meta["class"]
        self._attr_native_unit_of_measurement = self._meta["unit"]
        if self._attr_device_class == "energy":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        device.register_entity(self, value_key)

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
        return self._device.iot_id + "." + self._entity_identifier

    @property
    def name(self):
        prefix = next(
            iter(self._device.identifier_names.values()),
            self._device.nick_name or self._device.product_name,
        )
        return f"{prefix}{self._meta['name']}"

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def native_value(self):
        value = self._device.identifier_values.get(self._value_key)
        if value is None:
            return None
        if "value_map" in self._meta:
            value = self._meta["value_map"].get(value, value)
        if "scale" in self._meta:
            value /= self._meta["scale"]
        return value


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []
    sensor_specs = list(_iter_sensor_specs(SENSOR_MAPPING))
    ble_value_keys = {
        "WorkState",
        "GunState",
        "DeviceFaultCodeInfo",
        "DeviceRealInfo.ChargingTime",
        "DeviceRealInfo.ChargeVoltage",
        "DeviceRealInfo.ChargeCurrent",
        "DeviceRealInfo.ChargeActivePower",
        "DeviceRealInfo.ChargeEnergyUsed",
        "DeviceRealInfo.ChargeMBTemp",
        "DeviceRealInfo.ChargeSlotTemp",
        "DeviceRealInfo.ChargeGunTemp",
    }

    for device in bull_api.device_list.values():
        if device.global_product_id in SWITCH_PRODUCT_ID | CHARGER_PRODUCT_ID:
            for entity_identifier, value_key, meta in sensor_specs:
                if device.ble_charger and value_key == "ChargeMode":
                    continue
                if value_key in device.identifier_values or (
                    device.ble_charger and value_key in ble_value_keys
                ):
                    entities.append(
                        BullSensorEntity(device, entity_identifier, value_key, meta)
                    )

    async_add_entities(entities, update_before_add=False)
