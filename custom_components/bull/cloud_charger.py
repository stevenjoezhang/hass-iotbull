"""Cloud Thing Service control for Bull charging stations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from .api import BullSwitch


WORK_STATE_IDENTIFIER = "DeviceState.WorkState"
GUN_STATE_IDENTIFIER = "DeviceState.GunState"

WORK_STATE_STARTING = 1
WORK_STATE_CHARGING = 2
WORK_STATE_STOPPING = 3
WORK_STATE_LOW_POWER_CHARGING = 10
GUN_STATE_NOT_PLUGGED = 0

APP_START_CHARGE = "AppStartCharge"
APP_STOP_CHARGE = "AppStopCharge"
COMMAND_TIMEOUT = 10


class BullCloudServiceCharger:
    """Control PID 193/195 using the same Thing Services as MosHome."""

    def __init__(self, device: BullSwitch) -> None:
        self._device = device
        self._command_lock = asyncio.Lock()

    @staticmethod
    def _as_int(value) -> int | None:
        """Normalize property values while rejecting non-numeric data."""
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @property
    def work_state(self) -> int | None:
        """Return the current App WorkState value, if available."""
        return self._as_int(self._device.identifier_values.get(WORK_STATE_IDENTIFIER))

    @property
    def is_charging(self) -> bool | None:
        """Match MosHome's isCharging definition for this product family."""
        work_state = self.work_state
        if work_state is None:
            return None
        return work_state in {WORK_STATE_CHARGING, WORK_STATE_LOW_POWER_CHARGING}

    async def async_set_charging(self, charging: bool) -> None:
        """Invoke the App service and wait for its expected state transition."""
        async with self._command_lock:
            if not self._device.available:
                raise HomeAssistantError("充电桩当前离线")

            gun_state = self._as_int(
                self._device.identifier_values.get(GUN_STATE_IDENTIFIER)
            )
            if gun_state == GUN_STATE_NOT_PLUGGED:
                # MosHome refuses the control action before choosing start/stop.
                raise HomeAssistantError("请插入充电枪")

            current_charging = self.is_charging
            if current_charging is charging:
                return

            service = APP_START_CHARGE if charging else APP_STOP_CHARGE
            expected_state = WORK_STATE_STARTING if charging else WORK_STATE_STOPPING
            if self.work_state == expected_state:
                return

            waiter = self._device.create_property_waiter(
                WORK_STATE_IDENTIFIER, {expected_state, str(expected_state)}
            )
            try:
                # The official App supplies only the service identifier; there
                # is no service value or synthetic ChargeSwitch write.
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    # MosHome starts its ten-second transition timer before it
                    # invokes the cloud service, so include request time too.
                    await self._device.invoke_thing_service(service)
                    await waiter
            except TimeoutError as err:
                raise HomeAssistantError(
                    f"{service} 已发送，但设备状态未在 {COMMAND_TIMEOUT} 秒内响应"
                ) from err
            finally:
                self._device.cancel_property_waiter(WORK_STATE_IDENTIFIER, waiter)
