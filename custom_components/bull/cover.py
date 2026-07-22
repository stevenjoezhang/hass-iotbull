"""Entity definition for cover devices."""

from dataclasses import dataclass
import logging

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS, COVER_PRODUCT_ID
from .api import BullCover

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoverProtocol:
    """Cloud property identifiers used by one cover."""

    control_identifier: str
    position_identifier: str


_COVER_PROTOCOLS = (
    CoverProtocol("curtainConrtol", "curtainPosition"),
    CoverProtocol("CurtainConrtol", "CurtainPosition"),
)


def _resolve_cover_protocol(device: BullCover) -> CoverProtocol | None:
    """Resolve exact cover identifiers from the cloud property schema."""
    raw_properties = device.raw_info.get("property", {})
    property_entries = (
        raw_properties.values() if isinstance(raw_properties, dict) else ()
    )
    identifiers = {
        entry["identifier"]
        for entry in property_entries
        if isinstance(entry, dict) and isinstance(entry.get("identifier"), str)
    }

    matches = [
        protocol
        for protocol in _COVER_PROTOCOLS
        if protocol.control_identifier in identifiers
        and protocol.position_identifier in identifiers
    ]
    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        _LOGGER.warning(
            "Cover %s exposes multiple supported property protocols: %s",
            device.iot_id,
            sorted(identifiers),
        )
    return None


# https://developers.home-assistant.io/docs/core/entity/cover
class BullCoverEntity(CoverEntity):
    """Representation of a Bull IoT cover."""

    def __init__(self, device: BullCover, protocol: CoverProtocol) -> None:
        self._device = device
        self._protocol = protocol
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
        return self._device.identifier_values.get(self._protocol.position_identifier)

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        position = self.current_cover_position
        return position == 0 if position is not None else None

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        await self._device.set_dp(self._protocol.control_identifier, 1)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        await self._device.set_dp(self._protocol.control_identifier, 0)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        await self._device.set_dp(self._protocol.control_identifier, 2)

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        await self._device.set_dp(
            self._protocol.position_identifier, kwargs[ATTR_POSITION]
        )


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
            protocol = _resolve_cover_protocol(device)
            if protocol is None:
                _LOGGER.warning(
                    "Skipping cover %s (PID %s): cloud properties do not contain "
                    "a supported control/position pair",
                    device.iot_id,
                    device.global_product_id,
                )
                continue
            entities.append(BullCoverEntity(device, protocol))

    async_add_entities(entities, update_before_add=False)
