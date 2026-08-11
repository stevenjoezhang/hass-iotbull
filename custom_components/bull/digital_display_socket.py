"""Shared helpers for PID 296 digital-display charging station."""

from __future__ import annotations

from typing import Any

DIGITAL_DISPLAY_SOCKET_PRODUCT_ID = {296}

DIGITAL_DISPLAY_SOCKET_PORTS = (
    "C1",
    "C2",
    "C3",
    "C4",
    "A1",
    "A2",
)

DIGITAL_DISPLAY_SOCKET_PORT_NAMES = {
    "C1": "USB-C 1",
    "C2": "USB-C 2",
    "C3": "USB-C 3",
    "C4": "USB-C 4",
    "A1": "USB-A 1",
    "A2": "USB-A 2",
}


def port_property_keys(port: str, suffix: str) -> tuple[str, str, str]:
    """Return every representation observed for one port property."""
    return f"{port}{suffix}", f"{port}.{suffix}", port


def port_property_value(values: dict[str, Any], port: str, suffix: str) -> Any:
    """Read a flat App/MQTT value or a nested rooms-API value."""
    direct_key, dotted_key, parent_key = port_property_keys(port, suffix)
    for key in (direct_key, dotted_key):
        if key in values:
            return values[key]

    parent_value = values.get(parent_key)
    if isinstance(parent_value, dict):
        return parent_value.get(suffix)
    if suffix == "PowerSwitch" and parent_value in (0, 1, False, True):
        return parent_value
    return None


def master_power_is_on(values: dict[str, Any]) -> bool:
    """Return whether guarded App controls can currently be used."""
    value = values.get("PowerSwitch")
    return value not in (0, False) if value is not None else True


def brightness_value(value: Any) -> float | None:
    """Validate the PID 296 App's native 1..100 brightness percentage."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(1, min(100, value))
