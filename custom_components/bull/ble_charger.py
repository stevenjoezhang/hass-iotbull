"""Home Assistant coordinator for the local Bull D3 charger BLE protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import logging
import struct
from typing import TYPE_CHECKING

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .ble import BleIdentity, BullBleError, BullBleSession, BusinessMessage

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.core import HomeAssistant

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
CONNECT_READY_TIMEOUT = 90
RECONNECT_INITIAL_DELAY = 5
RECONNECT_MAX_DELAY = 300

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
        hass: HomeAssistant,
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
        self._address = ":".join(
            identity.dn[index : index + 2] for index in range(0, 12, 2)
        )
        self._unsub: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._connection_lost = asyncio.Event()
        self._connected = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None
        self._client: BleakClient | None = None
        self._session: BullBleSession | None = None

    async def async_start(self) -> None:
        from homeassistant.components import bluetooth
        from homeassistant.components.bluetooth import BluetoothScanningMode

        def discovered(
            service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
        ) -> None:
            if dn_from_ble_address(service_info.address) != self._identity.dn:
                return
            self._address = service_info.address

        self._unsub = bluetooth.async_register_callback(
            self._hass,
            discovered,
            {"address": self._address, "connectable": True},
            BluetoothScanningMode.ACTIVE,
        )
        self._runner_task = self._hass.async_create_task(
            self._run(), f"Bull BLE connection {self._identity.dn}"
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self._stop_event.set()
        task = self._runner_task
        self._runner_task = None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._disconnect()

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

    def _best_ble_device(self) -> BLEDevice | None:
        """Return HA's currently preferred local or proxy route."""
        from homeassistant.components import bluetooth

        return bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )

    def _disconnected_callback(self, client: BleakClient) -> None:
        """Wake the owner task when the active GATT link disconnects."""
        self._hass.loop.call_soon_threadsafe(self._handle_disconnect, client)

    def _handle_disconnect(self, client: BleakClient) -> None:
        if client is self._client:
            if self._session:
                self._session.disconnected()
            self._connection_lost.set()

    async def _connect(self) -> None:
        """Select a fresh HA route, connect, authenticate, and read initial state."""
        ble_device = self._best_ble_device()
        if ble_device is None:
            raise BullBleError(
                "charger is currently not connectable through Home Assistant Bluetooth"
            )

        self._connection_lost.clear()
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self._device.nick_name or self._device.product_name or "Bull charger",
            disconnected_callback=self._disconnected_callback,
            max_attempts=3,
            ble_device_callback=lambda: self._best_ble_device() or ble_device,
        )
        self._client = client
        session = BullBleSession(
            client,
            self._identity,
            self._confirm_random,
            self._confirm_device,
            self._event,
        )
        self._session = session
        await session.async_start()
        async with self._lock:
            await self._refresh_session(session)

        if not client.is_connected or self._connection_lost.is_set():
            raise BullBleError("charger disconnected during BLE session setup")
        self._connected.set()
        self._device.set_ble_available(True)
        _LOGGER.info(
            "Authenticated persistent BLE session for %s at %s",
            self._device.iot_id,
            ble_device.address,
        )

    async def _disconnect(self) -> None:
        """Release the current protocol session and GATT connection."""
        async with self._lock:
            session = self._session
            client = self._client
            self._session = None
            self._client = None
            self._connected.clear()
            self._device.set_ble_available(False)

            if session:
                await session.async_stop()
            if client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:  # pragma: no cover - backend specific cleanup
                    _LOGGER.debug("Failed to disconnect Bull BLE client", exc_info=True)

    async def _monitor_connection(self, session: BullBleSession) -> None:
        """Wait for a physical disconnect or a protocol reader failure."""
        disconnected = asyncio.create_task(self._connection_lost.wait())
        stopped = asyncio.create_task(self._stop_event.wait())
        reader = asyncio.create_task(session.async_wait_stopped())
        tasks = {disconnected, stopped, reader}
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            if stopped in done and self._stop_event.is_set():
                return
            if disconnected in done:
                raise BullBleError("persistent BLE connection was disconnected")
            if reader in done:
                try:
                    await reader
                except asyncio.CancelledError as error:
                    raise BullBleError(
                        "persistent BLE notification reader stopped"
                    ) from error
                raise BullBleError("persistent BLE notification reader stopped")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def _run(self) -> None:
        """Own the persistent connection and reconnect with exponential backoff."""
        reconnect_delay = RECONNECT_INITIAL_DELAY
        try:
            while not self._stop_event.is_set():
                try:
                    await self._connect()
                    reconnect_delay = RECONNECT_INITIAL_DELAY
                    session = self._session
                    if session is None:
                        raise BullBleError("BLE session disappeared after setup")
                    await self._monitor_connection(session)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    _LOGGER.warning(
                        "Bull BLE connection for %s failed; retrying in %d seconds: %s",
                        self._device.iot_id,
                        reconnect_delay,
                        error,
                    )
                finally:
                    await self._disconnect()

                if self._stop_event.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=reconnect_delay
                    )
                except asyncio.TimeoutError:
                    pass
                reconnect_delay = min(reconnect_delay * 2, RECONNECT_MAX_DELAY)
        finally:
            await self._disconnect()

    async def _connected_session(self) -> BullBleSession:
        """Wait briefly for the persistent authenticated session."""
        try:
            await asyncio.wait_for(
                self._connected.wait(), timeout=CONNECT_READY_TIMEOUT
            )
        except asyncio.TimeoutError as error:
            raise BullBleError("charger BLE session is not connected") from error
        session = self._session
        if session is None or self._client is None or not self._client.is_connected:
            raise BullBleError("charger BLE session disconnected")
        return session

    async def _refresh_session(self, session: BullBleSession) -> None:
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

    async def async_refresh(self) -> None:
        """Refresh all state through the existing persistent session."""
        session = await self._connected_session()
        async with self._lock:
            try:
                await self._refresh_session(session)
            except (BullBleError, BleakError, OSError, ValueError):
                self._connection_lost.set()
                raise

    async def async_set_charging(self, enable: bool) -> None:
        """Run the same D3 start/stop preconditions and state checks as the App."""
        session = await self._connected_session()
        async with self._lock:
            if session is not self._session:
                raise BullBleError("charger BLE session changed before command")
            try:
                work_state = await session.request(ATTR_WORK_STATE)
                gun_state = await session.request(ATTR_GUN_STATE)
            except (BullBleError, BleakError, OSError, ValueError):
                self._connection_lost.set()
                raise

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

            try:
                await session.send_service(SERVICE_START if enable else SERVICE_STOP)
            except (BullBleError, BleakError, OSError, ValueError):
                self._connection_lost.set()
                raise

            # The App observes WorkState transitions; a matching service ACK is
            # optional and the persistent reader continues applying events while
            # this delayed read-back is pending.
            await asyncio.sleep(3)
            try:
                result = await session.request(ATTR_WORK_STATE)
            except (BullBleError, BleakError, OSError, ValueError) as error:
                self._connection_lost.set()
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
