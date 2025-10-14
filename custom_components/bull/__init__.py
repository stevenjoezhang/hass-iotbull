"""Entry for bull-iot integration."""

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.service import async_register_admin_service

from .api import BullApi
from .const import (
    DOMAIN,
    BULL_DEVICES,
    BULL_API_CLIENTS,
    SERVICE_RELOAD,
    SUPPORTED_PLATFORMS,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Bull IoT integration component."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][BULL_DEVICES] = {}
    hass.data[DOMAIN][BULL_API_CLIENTS] = {}

    # Support for reloading in Developer Tools
    async def _handle_reload_config(service):
        for bull_api in hass.data[DOMAIN][BULL_API_CLIENTS].values():
            bull_api.destroy()
            await bull_api.setup()

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload_config,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bull IoT integration from a config entry."""
    bull_api = BullApi(hass, entry.data)

    await bull_api.setup()
    hass.data[DOMAIN][BULL_API_CLIENTS][entry.entry_id] = bull_api

    for iot_id, device in bull_api.device_list.items():
        hass.data[DOMAIN][BULL_DEVICES][iot_id] = device

    await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload or reload a config entry."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS].pop(entry.entry_id, None)
    # Not registered if setup fails
    if not bull_api:
        return True

    bull_api.destroy()

    # FIXME: multiple entries may have the same device
    for iot_id in bull_api.device_list:
        hass.data[DOMAIN][BULL_DEVICES].pop(iot_id)

    await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS)
    return True
