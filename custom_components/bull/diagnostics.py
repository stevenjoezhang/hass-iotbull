"""Diagnostics support for the Bull IoT integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .api import BullDevice
from .const import BULL_API_CLIENTS, DOMAIN

TO_REDACT = {
    "Authorization",
    "access_token",
    "password",
    "refresh_token",
    "token",
}


def _device_diagnostics(device: BullDevice) -> dict[str, Any]:
    """Return a redacted snapshot that is not exposed through entity state."""
    return async_redact_data(
        {
            "identity": {
                "iot_id": device.iot_id,
                "global_product_id": device.global_product_id,
                "product_name": device.product_name,
                "model_name": device.model_name,
                "firmware_version": device.firmware_version,
                "nickName": device.nick_name,
                "roomName": device.room,
            },
            "availability": {
                "available": device.available,
                "ble_configured": device.ble_charger is not None,
                "ble_available": device.ble_available,
            },
            "identifier_values": device.identifier_values,
            "raw_info": device.raw_info,
            "raw_device_info": device.raw_device_info,
        },
        TO_REDACT,
    )


def _get_api(hass: HomeAssistant, entry: ConfigEntry):
    """Return the loaded API instance for a config entry, if available."""
    return hass.data.get(DOMAIN, {}).get(BULL_API_CLIENTS, {}).get(entry.entry_id)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Bull IoT config entry."""
    api = _get_api(hass, entry)
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": (
            [_device_diagnostics(device) for device in api.device_list.values()]
            if api
            else []
        ),
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for one Bull IoT device."""
    api = _get_api(hass, entry)
    if api is None:
        return {"error": "entry_not_loaded"}

    iot_id = next(
        (identifier for domain, identifier in device.identifiers if domain == DOMAIN),
        None,
    )
    bull_device = api.device_list.get(iot_id) if iot_id else None
    if bull_device is None:
        return {"error": "device_not_loaded"}
    return _device_diagnostics(bull_device)
