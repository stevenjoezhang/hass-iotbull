"""Device triggers for Bull integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers.state import (
    async_attach_trigger as async_attach_state_trigger,
)
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_FOR,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

from .const import DOMAIN

TRIGGER_TYPES = {
    "work_state_idle": {
        "sensor_key": "DeviceWorkState.WorkState",
        "to": "未工作",
    },
    "work_state_charging": {
        "sensor_key": "DeviceWorkState.WorkState",
        "to": "充电中",
    },
    "work_state_gun_inserted_not_activated": {
        "sensor_key": "DeviceWorkState.WorkState",
        "to": "已插枪未激活",
    },
    "work_state_gun_inserted_activated": {
        "sensor_key": "DeviceWorkState.WorkState",
        "to": "已插枪已激活",
    },
    "gun_state_unplugged": {
        "sensor_key": "DeviceWorkState.GunState",
        "to": "未插枪",
    },
    "gun_state_plugged": {
        "sensor_key": "DeviceWorkState.GunState",
        "to": "已插枪",
    },
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_ENTITY_ID): str,
        vol.Optional(CONF_FOR): cv.positive_time_period_dict,
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for a Bull device."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_device(
        registry,
        device_id,
        include_disabled_entities=False,
    )

    entities_by_key: dict[str, str] = {}
    for entry in entries:
        if entry.domain != "sensor":
            continue
        if not entry.unique_id:
            continue
        if entry.unique_id.endswith("DeviceWorkState.WorkState"):
            entities_by_key["DeviceWorkState.WorkState"] = entry.entity_id
        elif entry.unique_id.endswith("DeviceWorkState.GunState"):
            entities_by_key["DeviceWorkState.GunState"] = entry.entity_id

    triggers: list[dict[str, Any]] = []
    for trigger_type, config in TRIGGER_TYPES.items():
        entity_id = entities_by_key.get(config["sensor_key"])
        if not entity_id:
            continue
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_ENTITY_ID: entity_id,
                CONF_TYPE: trigger_type,
            }
        )

    return triggers


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: dict[str, Any]
) -> dict[str, vol.Schema]:
    """List trigger capabilities."""
    return {
        "extra_fields": vol.Schema(
            {vol.Optional(CONF_FOR): cv.positive_time_period_dict}
        )
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict[str, Any],
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    trigger = TRIGGER_TYPES[config[CONF_TYPE]]

    state_config: dict[str, Any] = {
        CONF_PLATFORM: "state",
        CONF_ENTITY_ID: config[CONF_ENTITY_ID],
    }
    if "to" in trigger:
        state_config["to"] = trigger["to"]
    if "from" in trigger:
        state_config["from"] = trigger["from"]
    if CONF_FOR in config:
        state_config[CONF_FOR] = config[CONF_FOR]

    return await async_attach_state_trigger(
        hass,
        state_config,
        action,
        trigger_info,
        platform_type="device",
    )
