"""Home Assistant coordinator for the local Bull D3 charger BLE protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
import struct
from typing import TYPE_CHECKING

from bleak.exc import BleakError

from .ble import BleIdentity, BullBleError, BullBleSession, BusinessMessage

if TYPE_CHECKING:
    from .api import BullDevice

_LOGGER = logging.getLogger(__name__)

ATTR_WORK_STATE = 0x0105
ATTR_REAL_INFO = 0x0209
ATTR_FAULT_INFO = 0x0301
ATTR_GUN_STATE = 0x0600
ATTR_CHARGE_MODE = 0x0624
SERVICE_START = 0x9000
SERVICE_STOP = 0x9001

WORK_STATES_CHARGING = frozenset((2, 10))
WORK_STATES_START_CONFIRMED = frozenset((1, 2, 10))
WORK_STATES_STOP_CONFIRMED = frozenset((0, 3, 4))
GUN_STATE_NOT_PLUGGED = 0

ConfirmRandom = Callable[[int, str], Awaitable[str]]
ConfirmDevice = Callable[[str, int, str, bytes], Awaitable[str]]


def dn_from_ble_address(address: str) -> str | None:
    """Apply the official Android ``macToDn`` conversion to a real BLE MAC."""
    dn = address.replace(":", "").upper()
    if len(dn) != 12 or any(char not in "0123456789ABCDEF" for char in dn):
        return None
    return dn


class BullBleCharger:
    """Bridge one cloud-authorized D3 charger to HA's Bluetooth stack.

    Matching is deliberately MAC-derived ``dn`` equality, never the mutable
    BULL-Charge display name.  This works for local adapters and ESPHome
    Bluetooth Proxy because HA supplies the routed ``BLEDevice``.
    """

    def __init__(
        self,
        hass,
        device: BullDevice,
        identity: BleIdentity,
        confirm_random: ConfirmRandom,
        confirm_device: ConfirmDevice,
    ) -> None:
        self._hass = hass
        self._device = device
        self._identity = identity
        self._confirm_random = confirm_random
        self._confirm_device = confirm_device
        self._address = None
        self._unsub = None
        self._unsub_poll = None
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0

    async def async_start(self) -> None:
        from homeassistant.components import bluetooth
        from homeassistant.components.bluetooth import BluetoothScanningMode
        from homeassistant.helpers.event import async_track_time_interval

        def discovered(service_info, _change) -> None:
            if dn_from_ble_address(service_info.address) != self._identity.dn:
                return
            self._address = service_info.address
            self._hass.async_create_task(self.async_refresh())

        self._unsub = bluetooth.async_register_callback(
            self._hass, discovered, {"connectable": True}, BluetoothScanningMode.ACTIVE
        )
        self._unsub_poll = async_track_time_interval(
            self._hass,
            lambda _now: self._hass.async_create_task(self.async_refresh()),
            timedelta(minutes=5),
        )
        for service_info in bluetooth.async_discovered_service_info(
            self._hass, connectable=True
        ):
            discovered(service_info, None)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_poll:
            self._unsub_poll()
            self._unsub_poll = None

    async def _event(self, message: BusinessMessage) -> None:
        if message.attr_type is not None:
            self._apply(message.attr_type, message.value)

    def _apply(self, attr_type: int, value: bytes) -> None:
        if attr_type in (ATTR_WORK_STATE, ATTR_GUN_STATE, ATTR_CHARGE_MODE):
            if value:
                name = {
                    ATTR_WORK_STATE: "WorkState",
                    ATTR_GUN_STATE: "GunState",
                    ATTR_CHARGE_MODE: "ChargeMode",
                }[attr_type]
                self._device.update_dp(name, value[0])
                if attr_type == ATTR_WORK_STATE:
                    self._device.update_dp("ChargeSwitch", int(value[0] in (2, 10)))
        elif attr_type == ATTR_FAULT_INFO:
            self._device.update_dp(
                "DeviceFaultCodeInfo", value.decode(errors="replace").rstrip("\x00")
            )
        elif attr_type == ATTR_REAL_INFO and len(value) >= 32:
            names = (
                "ChargingTime",
                "ChargeVoltage",
                "ChargeCurrent",
                "ChargeActivePower",
                "ChargeEnergyUsed",
                "ChargeMBTemp",
                "ChargeSlotTemp",
                "ChargeGunTemp",
            )
            for name, item in zip(
                names, struct.unpack("<I7f", value[:32]), strict=True
            ):
                self._device.update_dp(f"DeviceRealInfo.{name}", item)

    async def _session(self) -> BullBleSession:
        from homeassistant.components import bluetooth

        if not self._address:
            raise BullBleError(
                "charger has not been discovered by Home Assistant Bluetooth"
            )
        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device is None:
            raise BullBleError(
                "charger is currently not connectable through Home Assistant Bluetooth"
            )
        return BullBleSession(
            ble_device,
            self._identity,
            self._confirm_random,
            self._confirm_device,
            self._event,
        )

    async def async_refresh(self) -> None:
        now = asyncio.get_running_loop().time()
        if not self._address or self._lock.locked() or now - self._last_refresh < 30:
            return
        async with self._lock:
            self._last_refresh = now
            try:
                session = await self._session()
                async with session:
                    for attr in (
                        ATTR_WORK_STATE,
                        ATTR_GUN_STATE,
                        ATTR_CHARGE_MODE,
                        ATTR_REAL_INFO,
                        ATTR_FAULT_INFO,
                    ):
                        result = await session.request(attr)
                        if result.attr_type is not None:
                            self._apply(result.attr_type, result.value)
                self._device.ble_available = True
            except (BullBleError, BleakError, OSError, ValueError) as error:
                self._device.ble_available = False
                _LOGGER.debug(
                    "BLE refresh for %s failed: %s", self._device.iot_id, error
                )
            finally:
                self._device.update_dp(
                    "status", self._device.identifier_values.get("status", "OFFLINE")
                )

    async def async_set_charging(self, enable: bool) -> None:
        """Run the same D3 start/stop preconditions and state checks as the App."""
        async with self._lock:
            session = await self._session()
            async with session:
                work_state = await session.request(ATTR_WORK_STATE)
                gun_state = await session.request(ATTR_GUN_STATE)
                if not work_state.value or not gun_state.value:
                    raise BullBleError(
                        "charger returned an empty WorkState or GunState value"
                    )
                work_value = work_state.value[0]
                gun_value = gun_state.value[0]
                if gun_value == GUN_STATE_NOT_PLUGGED:
                    raise BullBleError(
                        f"refusing {'start' if enable else 'stop'}: "
                        f"GunState={gun_value} (not plugged)"
                    )
                if enable and work_value in WORK_STATES_CHARGING:
                    raise BullBleError(
                        f"refusing start: WorkState={work_value} (already charging)"
                    )
                if not enable and work_value not in WORK_STATES_CHARGING:
                    raise BullBleError(
                        f"refusing stop: WorkState={work_value} (not charging)"
                    )
                await session.send_service(SERVICE_START if enable else SERVICE_STOP)
                # The App observes WorkState transitions; a matching service ACK
                # is optional and some firmware omits it.  Events are delivered
                # through the session callback while this read-back is pending.
                await asyncio.sleep(3)
                try:
                    result = await session.request(ATTR_WORK_STATE)
                except BullBleError as error:
                    _LOGGER.info(
                        "BLE charging command for %s sent; WorkState read-back unavailable: %s",
                        self._device.iot_id,
                        error,
                    )
                else:
                    if result.attr_type is not None:
                        self._apply(result.attr_type, result.value)
                        if result.value:
                            observed = result.value[0]
                            expected = (
                                WORK_STATES_START_CONFIRMED
                                if enable
                                else WORK_STATES_STOP_CONFIRMED
                            )
                            if observed in expected:
                                _LOGGER.info(
                                    "BLE charging command for %s confirmed by WorkState=%d",
                                    self._device.iot_id,
                                    observed,
                                )
                            else:
                                _LOGGER.warning(
                                    "BLE charging command for %s not confirmed: WorkState=%d",
                                    self._device.iot_id,
                                    observed,
                                )
            self._device.ble_available = True
            self._device.update_dp(
                "status", self._device.identifier_values.get("status", "OFFLINE")
            )
