from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .api import BullApi
from .const import DOMAIN, DATA_CLOUD, BULL_DEVICES


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Bull IoT integration component."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][BULL_DEVICES] = {}

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bull IoT integration from a config entry."""
    bull_api = BullApi(hass, entry.data)

    await bull_api.async_get_all_devices_list()

    #hass.data[DOMAIN][DATA_CLOUD] = bull_api

    async def setup_entities(device_ids):
        for dev_id in device_ids:
            hass.data[DOMAIN][BULL_DEVICES][dev_id] = bull_api.device_list[dev_id]

        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, "switch"))

        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, "sensor"))

        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, "cover"))

        await bull_api.init_mqtt()

    hass.async_create_task(setup_entities(bull_api.device_list.keys()))

    return True
