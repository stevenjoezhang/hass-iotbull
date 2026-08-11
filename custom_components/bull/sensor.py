"""Entity definition for sensor devices."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor.const import SensorStateClass

from .const import (
    DOMAIN,
    BULL_API_CLIENTS,
    SWITCH_PRODUCT_ID,
    SENSOR_MAPPING,
    CHARGER_PRODUCT_ID,
    SENSOR_PRODUCT_ID,
)
from .api import BullDevice
from .digital_display_socket import (
    DIGITAL_DISPLAY_SOCKET_PORT_NAMES,
    DIGITAL_DISPLAY_SOCKET_PORTS,
    DIGITAL_DISPLAY_SOCKET_PRODUCT_ID,
    port_property_keys,
    port_property_value,
)

DIGITAL_DISPLAY_SOCKET_MASTER_SENSORS = (
    "RealTimePower",
    "RealTimeVoltage",
    "RealTimeCurrent",
    "TotalConsumption",
    "PowerOnDuration",
    "PowerLevel",
)

DIGITAL_DISPLAY_SOCKET_PORT_SENSORS = {
    "RealTimePower": SENSOR_MAPPING["RealTimePower"],
    "RealTimeVoltage": SENSOR_MAPPING["RealTimeVoltage"],
    "RealTimeCurrent": SENSOR_MAPPING["RealTimeCurrent"],
    "Duration": {
        "name": "供电时长",
        "unit": UnitOfTime.MINUTES,
        "class": "duration",
    },
    "CurrentProtocol": {
        "name": "快充协议",
        "unit": None,
        "class": None,
        "value_map": {
            0: "未连接",
            1: "PD",
            2: "UFCS",
            3: "QC 2.0",
            4: "QC 3.0",
            5: "QC 3+",
            6: "FCP",
            7: "SCP",
            8: "SFCP",
            9: "TFCP",
            10: "AFC",
            11: "PE",
            12: "Xiaomi",
        },
    },
}


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

    _attr_should_poll = False

    def __init__(
        self,
        device: BullDevice,
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
        if translation_key := self._meta.get("translation_key"):
            self._attr_has_entity_name = True
            self._attr_translation_key = translation_key
        if self._attr_device_class == "energy":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif self._attr_device_class == "battery":
            self._attr_state_class = SensorStateClass.MEASUREMENT
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
        if "translation_key" in self._meta:
            return None
        identifier_names = getattr(self._device, "identifier_names", {})
        prefix = next(
            iter(identifier_names.values()),
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


class BullDigitalDisplaySocketSensorEntity(BullSensorEntity):
    """Master telemetry sensor for PID 296."""

    @property
    def name(self):
        return f"{self._device.nick_name}{self._meta['name']}"


class BullDigitalDisplaySocketPortSensorEntity(BullSensorEntity):
    """Telemetry sensor for one PID 296 USB port."""

    def __init__(self, device: BullDevice, port: str, suffix: str, meta: dict):
        self._port = port
        self._suffix = suffix
        identifier, dotted_identifier, parent_identifier = port_property_keys(
            port, suffix
        )
        super().__init__(device, identifier, identifier, meta)
        device.register_entity(self, dotted_identifier, parent_identifier)

    @property
    def name(self):
        return (
            f"{self._device.nick_name}"
            f"{DIGITAL_DISPLAY_SOCKET_PORT_NAMES[self._port]}"
            f"{self._meta['name']}"
        )

    @property
    def native_value(self):
        value = port_property_value(
            self._device.identifier_values, self._port, self._suffix
        )
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
    cloud_charger_value_keys = {
        "DeviceState.WorkState",
        "DeviceState.GunState",
        "DeviceRealInfo.EnergyUsed",
        "DeviceRealInfo.Current",
        "DeviceRealInfo.Voltage",
        "DeviceRealInfo.ActivePower",
        "DeviceRealInfo.SlotTemp",
        "DeviceRealInfo.MBTemp",
    }

    for device in bull_api.device_list.values():
        if device.global_product_id in DIGITAL_DISPLAY_SOCKET_PRODUCT_ID:
            entities.extend(
                BullDigitalDisplaySocketSensorEntity(
                    device,
                    identifier,
                    identifier,
                    SENSOR_MAPPING[identifier],
                )
                for identifier in DIGITAL_DISPLAY_SOCKET_MASTER_SENSORS
            )
            entities.extend(
                BullDigitalDisplaySocketPortSensorEntity(device, port, suffix, meta)
                for port in DIGITAL_DISPLAY_SOCKET_PORTS
                for suffix, meta in DIGITAL_DISPLAY_SOCKET_PORT_SENSORS.items()
            )
            continue
        if device.global_product_id in (
            SWITCH_PRODUCT_ID | CHARGER_PRODUCT_ID | SENSOR_PRODUCT_ID
        ):
            for entity_identifier, value_key, meta in sensor_specs:
                if device.ble_charger and value_key == "ChargeMode":
                    continue
                if (
                    value_key in device.identifier_values
                    or (device.ble_charger and value_key in ble_value_keys)
                    or (device.cloud_charger and value_key in cloud_charger_value_keys)
                ):
                    entities.append(
                        BullSensorEntity(device, entity_identifier, value_key, meta)
                    )

    async_add_entities(entities, update_before_add=False)
