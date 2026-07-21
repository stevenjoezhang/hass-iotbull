"""Entity definition for button devices."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, BULL_API_CLIENTS, BUTTON_ENTITY_MAPPING
from .api import BullSwitch


def _matches_condition(identifier_values: dict, condition: dict) -> bool:
    all_conditions = condition.get("all")
    if all_conditions is not None:
        return all(
            all(
                identifier_values.get(key) == expected for key, expected in item.items()
            )
            for item in all_conditions
        )

    any_conditions = condition.get("any")
    if any_conditions is not None:
        return any(
            all(
                identifier_values.get(key) == expected for key, expected in item.items()
            )
            for item in any_conditions
        )

    return all(
        identifier_values.get(key) == expected for key, expected in condition.items()
    )


class BullMappedButtonEntity(ButtonEntity):
    """Representation of a Bull IoT mapped button."""

    def __init__(
        self,
        device: BullSwitch,
        entity_identifier: str,
        name: str,
        service_identifier: str,
        available_condition: dict | None,
    ) -> None:
        self._device = device
        self._identifier = entity_identifier
        self._name = name
        self._service_identifier = service_identifier
        self._available_condition = available_condition
        self._attr_should_poll = False
        if self not in self._device._button_entities:
            self._device._button_entities.append(self)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device.iot_id)},
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
        return self._device.iot_id + "." + self._identifier

    @property
    def name(self) -> str:
        return self._name

    @property
    def available(self) -> bool:
        if not self._device.available:
            return False
        if not self._available_condition:
            return True
        return _matches_condition(
            self._device.identifier_values, self._available_condition
        )

    async def async_press(self) -> None:
        await self._device.invoke_thing_service(self._service_identifier)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Bull IoT button platform."""
    bull_api = hass.data[DOMAIN][BULL_API_CLIENTS][config_entry.entry_id]
    entities = []

    for device in bull_api.device_list.values():
        button_configs = BUTTON_ENTITY_MAPPING.get(device.global_product_id, [])
        for config in button_configs:
            entities.append(
                BullMappedButtonEntity(
                    device,
                    config["entity_identifier"],
                    config["name"],
                    config["service_identifier"],
                    config.get("available_condition"),
                )
            )

    async_add_entities(entities, update_before_add=False)
