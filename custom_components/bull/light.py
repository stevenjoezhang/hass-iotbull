"""Entity definitions for Bull light devices."""

from dataclasses import dataclass
import logging
from math import floor, isfinite
from numbers import Real

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BullDevice
from .const import BULL_API_CLIENTS, DOMAIN, LIGHT_PRODUCT_ID

_LOGGER = logging.getLogger(__name__)

POWER_SWITCH = "PowerSwitch"
BRIGHT_VALUE = "BrightValue"
COLOR_TEMPERATURE = "ColorTemperature"

_REQUIRED_IDENTIFIERS = {POWER_SWITCH, BRIGHT_VALUE, COLOR_TEMPERATURE}
_SINGLE_LAMP_MARKERS = {
    "OffRampTime",
    "OnRampTime",
    "PowerSwitchSave",
    "ThreeDimmerEnable",
    "TestMode",
}
_WIFI_LAMP_MARKERS = {"PowerGradation", "PowerOffMemory"}

_DEFAULT_MIN_COLOR_TEMP_KELVIN = 3000
_DEFAULT_MAX_COLOR_TEMP_KELVIN = 5700


def _round_positive(value: float) -> int:
    """Match JavaScript's rounding for the protocol's non-negative values."""
    return floor(value + 0.5)


@dataclass(frozen=True)
class LightProtocol:
    """Property encoding used by one light family."""

    family: str

    def raw_to_brightness(self, raw_value: int | float) -> int:
        """Convert the App's device brightness to Home Assistant's 1..255."""
        if self.family == "wifi_lamp":
            percentage = (float(raw_value) - 10) / 990
        else:
            percentage = float(raw_value) / 1000
        return max(1, min(255, _round_positive(percentage * 255)))

    def brightness_to_raw(self, brightness: int) -> int:
        """Convert Home Assistant's 1..255 brightness to the device value."""
        percentage = max(1, min(255, int(brightness))) / 255
        if self.family == "wifi_lamp":
            return int(10 + percentage * 990)
        return max(10, _round_positive(percentage * 1000))


def _resolve_light_protocol(device: BullDevice) -> LightProtocol | None:
    """Resolve the brightness encoding from the cloud property fingerprint."""
    identifiers = set(device.identifier_values)
    if not _REQUIRED_IDENTIFIERS.issubset(identifiers):
        return None

    single_markers = identifiers & _SINGLE_LAMP_MARKERS
    wifi_markers = identifiers & _WIFI_LAMP_MARKERS
    if single_markers and not wifi_markers:
        return LightProtocol("single_lamp")
    if wifi_markers and not single_markers:
        return LightProtocol("wifi_lamp")

    _LOGGER.warning(
        "Cannot resolve light protocol for %s (PID %s): single markers=%s, "
        "Wi-Fi markers=%s",
        device.iot_id,
        device.global_product_id,
        sorted(single_markers),
        sorted(wifi_markers),
    )
    return None


def _as_number(value) -> float | None:
    """Return a finite numeric protocol value without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        return number if isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if isfinite(number) else None
    return None


def _find_named_value(data, identifier: str):
    """Find a value in either identifier/value entries or nested device data."""
    if isinstance(data, dict):
        if data.get("identifier") == identifier and "value" in data:
            return data["value"]
        if identifier in data:
            value = data[identifier]
            if isinstance(value, dict) and "value" in value:
                return value["value"]
            return value
        for value in data.values():
            found = _find_named_value(value, identifier)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_named_value(value, identifier)
            if found is not None:
                return found
    return None


def _color_temperature_limits(device: BullDevice) -> tuple[int, int]:
    """Return the App's device-specific Kelvin limits, with its defaults."""
    lower = _as_number(device.identifier_values.get("cctLowerLimit"))
    upper = _as_number(device.identifier_values.get("cctUpperLimit"))
    if lower is None:
        lower = _as_number(_find_named_value(device.raw_info, "cctLowerLimit"))
    if lower is None:
        lower = _as_number(_find_named_value(device.raw_device_info, "cctLowerLimit"))
    if upper is None:
        upper = _as_number(_find_named_value(device.raw_info, "cctUpperLimit"))
    if upper is None:
        upper = _as_number(_find_named_value(device.raw_device_info, "cctUpperLimit"))

    if (
        lower is None
        or upper is None
        or lower < 1000
        or upper > 10000
        or lower >= upper
    ):
        return (
            _DEFAULT_MIN_COLOR_TEMP_KELVIN,
            _DEFAULT_MAX_COLOR_TEMP_KELVIN,
        )
    return _round_positive(lower), _round_positive(upper)


class BullLightEntity(LightEntity):
    """Representation of a Bull dimmable, tunable-white light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}

    def __init__(self, device: BullDevice, protocol: LightProtocol) -> None:
        self._device = device
        self._protocol = protocol
        self._attr_unique_id = f"{device.iot_id}.light"
        self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin = (
            _color_temperature_limits(device)
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.iot_id)},
            "name": f"{device.room}{device.nick_name}",
            "manufacturer": "Bull",
            "model": device.product_name,
            "model_id": device.model_name,
            "serial_number": device.iot_id,
            "suggested_area": device.room,
            "sw_version": device.firmware_version,
        }
        device.register_entity(
            self,
            POWER_SWITCH,
            BRIGHT_VALUE,
            COLOR_TEMPERATURE,
        )

    @property
    def available(self) -> bool:
        """Return whether the cloud device is online."""
        return self._device.available

    @property
    def is_on(self) -> bool:
        """Return whether the light is on."""
        raw_value = self._device.identifier_values.get(POWER_SWITCH)
        if isinstance(raw_value, bool):
            return raw_value
        value = _as_number(raw_value)
        return value is not None and value != 0

    @property
    def color_mode(self) -> ColorMode:
        """Return the active Home Assistant color mode."""
        return ColorMode.COLOR_TEMP

    @property
    def brightness(self) -> int | None:
        """Return brightness in Home Assistant's 1..255 range."""
        value = _as_number(self._device.identifier_values.get(BRIGHT_VALUE))
        if value is None:
            return None
        return self._protocol.raw_to_brightness(value)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature in Kelvin."""
        value = _as_number(self._device.identifier_values.get(COLOR_TEMPERATURE))
        if value is None:
            return None
        percentage = max(0, min(1000, value)) / 1000
        return _round_positive(
            self.min_color_temp_kelvin
            + percentage * (self.max_color_temp_kelvin - self.min_color_temp_kelvin)
        )

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the light and apply optional brightness and color temperature."""
        properties = {POWER_SWITCH: 1}
        if ATTR_BRIGHTNESS in kwargs:
            properties[BRIGHT_VALUE] = self._protocol.brightness_to_raw(
                kwargs[ATTR_BRIGHTNESS]
            )
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(
                self.min_color_temp_kelvin,
                min(self.max_color_temp_kelvin, kwargs[ATTR_COLOR_TEMP_KELVIN]),
            )
            percentage = (kelvin - self.min_color_temp_kelvin) / (
                self.max_color_temp_kelvin - self.min_color_temp_kelvin
            )
            properties[COLOR_TEMPERATURE] = _round_positive(percentage * 1000)
        await self._device.set_dps(properties)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the light."""
        await self._device.set_dp(POWER_SWITCH, 0)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bull light entities."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []
    for device in bull_api.device_list.values():
        if device.global_product_id not in LIGHT_PRODUCT_ID:
            continue
        protocol = _resolve_light_protocol(device)
        if protocol is None:
            _LOGGER.warning(
                "Skipping light %s (PID %s): unsupported cloud property schema",
                device.iot_id,
                device.global_product_id,
            )
            continue
        entities.append(BullLightEntity(device, protocol))

    async_add_entities(entities, update_before_add=False)
