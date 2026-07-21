"""Entry for bull-iot integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.service import async_register_admin_service

from .api import AuthenticationError, BullApi, CloudApiError, NetworkError
from .const import (
    DOMAIN,
    BULL_API_CLIENTS,
    SERVICE_RELOAD,
    SUPPORTED_PLATFORMS,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Bull IoT integration component."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(BULL_API_CLIENTS, {})

    # Support for reloading in Developer Tools
    async def _handle_reload_config(_service: ServiceCall) -> None:
        entry_ids = list(hass.data[DOMAIN][BULL_API_CLIENTS])
        for entry_id in entry_ids:
            await hass.config_entries.async_reload(entry_id)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload_config,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bull IoT integration from a config entry."""

    bull_api = BullApi(hass, entry.data, entry=entry)

    try:
        await bull_api.setup()
    except AuthenticationError as err:
        await bull_api.async_destroy()
        raise ConfigEntryAuthFailed(str(err)) from err
    except (CloudApiError, NetworkError) as err:
        await bull_api.async_destroy()
        raise ConfigEntryNotReady(str(err)) from err
    hass.data[DOMAIN][BULL_API_CLIENTS][entry.entry_id] = bull_api

    try:
        await hass.config_entries.async_forward_entry_setups(entry, SUPPORTED_PLATFORMS)
    except Exception:
        hass.data[DOMAIN][BULL_API_CLIENTS].pop(entry.entry_id, None)
        await bull_api.async_destroy()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload or reload a config entry."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS].get(entry.entry_id)
    # Not registered if setup fails
    if not bull_api:
        return True

    if not await hass.config_entries.async_unload_platforms(entry, SUPPORTED_PLATFORMS):
        return False
    hass.data[DOMAIN][BULL_API_CLIENTS].pop(entry.entry_id, None)
    await bull_api.async_destroy()
    return True
