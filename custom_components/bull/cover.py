from .const import DOMAIN, BULL_DEVICES, COVER_PRODUCT_ID
from .api import BullDevice
from homeassistant.components.cover import CoverEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

# https://developers.home-assistant.io/docs/core/entity/cover
class BullCoverEntity(CoverEntity):
    def __init__(self, device: BullDevice) -> None:
        self._device = device
        device._entity = self

    @property
    def device_info(self):
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._device._iotId)
            },
            "name": self._device._official_product_name,
            "manufacturer": "Bull",
            "model": self._device._official_product_name
        }

    @property
    def unique_id(self) -> str:
        return self._device._iotId

    @property
    def name(self) -> str:
        return self._device._name

    @property
    def should_poll(self):
        """Return if platform should poll for updates."""
        return False

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def current_cover_position(self) -> int:
        """Return the current position of cover where 0 means closed and 100 is fully open."""
        return self._device._identifier_values["curtainPosition"]

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed."""
        return self._device._identifier_values["curtainPosition"] == 0

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        await self._device.set_dp("curtainConrtol", 1)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        await self._device.set_dp("curtainConrtol", 0)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self._device.set_dp("curtainConrtol", 2)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Bull IoT platform."""
    entities = []
    for device in hass.data[DOMAIN][BULL_DEVICES].values():
        if device._global_product_id in COVER_PRODUCT_ID:
            entities.append(BullCoverEntity(device))

    async_add_entities(entities, update_before_add=False)
