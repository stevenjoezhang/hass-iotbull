"""Entity definition for cover devices."""

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS, COVER_PRODUCT_ID
from .api import BullDevice


# https://developers.home-assistant.io/docs/core/entity/cover
class BullCoverEntity(CoverEntity):
    """Representation of a Bull IoT cover."""

    def __init__(self, device: BullDevice) -> None:
        self._device = device
        self._attr_should_poll = False
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.STOP
        )
        device._entity = self

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
        return self._device.iot_id

    @property
    def name(self) -> str:
        return self._device.name

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._device.available

    @property
    def current_cover_position(self) -> int | None:
        """Return the current position of cover where 0 means closed and 100 is fully open."""
        return self._device.identifier_values.get("curtainPosition")

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        position = self.current_cover_position
        return position == 0 if position is not None else None

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        await self._device.set_dp("curtainConrtol", 1)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        await self._device.set_dp("curtainConrtol", 0)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self._device.set_dp("curtainConrtol", 2)

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        await self._device.set_dp("curtainPosition", kwargs[ATTR_POSITION])


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []
    for device in bull_api.device_list.values():
        if device.global_product_id in COVER_PRODUCT_ID:
            entities.append(BullCoverEntity(device))

    async_add_entities(entities, update_before_add=False)
