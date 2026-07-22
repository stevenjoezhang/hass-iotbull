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
        "DeviceWorkState.WorkState": "未工作",
        "DeviceState.WorkState": "待机",
        "WorkState": "待机",
    },
    "work_state_charging": {
        "DeviceWorkState.WorkState": "充电中",
        "DeviceState.WorkState": "充电中",
        "WorkState": "充电中",
    },
    "work_state_gun_inserted_not_activated": {
        "DeviceWorkState.WorkState": "已插枪未激活",
    },
    "work_state_gun_inserted_activated": {
        "DeviceWorkState.WorkState": "已插枪已激活",
    },
    "gun_state_unplugged": {
        "DeviceWorkState.GunState": "未插枪",
        "DeviceState.GunState": "未插枪",
        "GunState": "未插枪",
    },
    "gun_state_plugged": {
        "DeviceWorkState.GunState": "已插枪",
        "DeviceState.GunState": "已插枪",
        "GunState": "已插枪",
    },
}

SENSOR_KEYS = tuple(
    sorted(
        {sensor_key for states in TRIGGER_TYPES.values() for sensor_key in states},
        key=len,
        reverse=True,
    )
)


def _sensor_key_from_unique_id(unique_id: str) -> str | None:
    """Resolve legacy nested and PID 309 flat sensor identifiers."""
    return next(
        (key for key in SENSOR_KEYS if unique_id.endswith(f".{key}")),
        None,
    )


def _trigger_state_from_unique_id(trigger_type: str, unique_id: str) -> str | None:
    """Return the state associated with a trigger for one sensor variant."""
    sensor_key = _sensor_key_from_unique_id(unique_id)
    if sensor_key is None:
        return None
    return TRIGGER_TYPES[trigger_type].get(sensor_key)


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
        sensor_key = _sensor_key_from_unique_id(entry.unique_id)
        if sensor_key:
            entities_by_key[sensor_key] = entry.entity_id

    triggers: list[dict[str, Any]] = []
    for trigger_type, states_by_sensor_key in TRIGGER_TYPES.items():
        for sensor_key in states_by_sensor_key:
            entity_id = entities_by_key.get(sensor_key)
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
            break

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
    registry = er.async_get(hass)
    entity = registry.async_get(config[CONF_ENTITY_ID])
    target_state = (
        _trigger_state_from_unique_id(config[CONF_TYPE], entity.unique_id)
        if entity and entity.unique_id
        else None
    )
    if target_state is None:
        raise vol.Invalid("trigger entity does not match its configured type")

    state_config: dict[str, Any] = {
        CONF_PLATFORM: "state",
        CONF_ENTITY_ID: config[CONF_ENTITY_ID],
        "to": target_state,
    }
    if CONF_FOR in config:
        state_config[CONF_FOR] = config[CONF_FOR]

    return await async_attach_state_trigger(
        hass,
        state_config,
        action,
        trigger_info,
        platform_type="device",
    )
