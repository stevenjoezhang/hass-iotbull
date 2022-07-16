from .const import DOMAIN, BULL_DEVICES
from .api import BullDevice
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry


class PowerSwitch(SwitchEntity):
    def __init__(self, device: BullDevice) -> None:
        self._device = device
        device.entity = self

    @property
    def unique_id(self) -> str:
        return self._device.unique_id

    @property
    def name(self) -> str:
        return self._device.name

    @property
    def should_poll(self):
        """Return if platform should poll for updates."""
        return False

    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return self._device.is_on

    async def async_turn_on(self, **kwargs):
        """Turn Bull IoT switch on."""
        await self._device.set_dp(True)

    async def async_turn_off(self, **kwargs):
        """Turn Bull IoT switch off."""
        await self._device.set_dp(False)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Awesome Light platform."""
    entities = [PowerSwitch(device)
                for device in hass.data[DOMAIN][BULL_DEVICES].values()]
    async_add_entities(entities, update_before_add=True)
