"""Local FEB3 BLE transport for the Bull D3-B32EB charger.

The protocol session deliberately accepts an already-connected ``BleakClient``
created from Home Assistant's preferred Bluetooth route.  It never starts its
own scanner or owns reconnection, so local adapters and ESPHome Bluetooth
Proxies remain interchangeable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000feb3-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fed5-0000-1000-8000-00805f9b34fb"
WRITE_NO_RESPONSE_UUID = "0000fed7-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fed8-0000-1000-8000-00805f9b34fb"
INDICATE_UUID = "0000fed6-0000-1000-8000-00805f9b34fb"

CMD_EVENT = 0x01
CMD_REQUEST = 0x02
CMD_RESPONSE = 0x03
CMD_BUBBLING_EVENT = 0x0C
CMD_AUTH_RANDOM = 0x10
CMD_AUTH_CIPHER = 0x11
CMD_AUTH_RESULT = 0x12
CMD_AUTH_DONE = 0x13
RESPONSE_OPCODE = 0xD3


class BullBleError(RuntimeError):
    """A local Gongniu BLE operation failed."""


@dataclass(frozen=True, slots=True)
class BleIdentity:
    """Cloud-authorized identity of one physical charger."""

    pid: int
    dn: str


@dataclass(frozen=True, slots=True)
class BusinessMessage:
    """Decoded encrypted FEB3 response/event."""

    command: int
    message_id: int
    opcode: int | None
    transaction_id: int | None
    attr_type: int | None
    value: bytes


@dataclass(frozen=True, slots=True)
class _PendingRequest:
    """Expected identifiers and waiter for one business response."""

    transaction_id: int
    attr_type: int
    future: asyncio.Future[BusinessMessage]


def _pad(data: bytes) -> bytes:
    amount = 16 - len(data) % 16
    return data + bytes((amount,)) * amount


def _encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(_pad(data)) + encryptor.finalize()


def _decrypt(data: bytes, clear_length: int, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    pad = padded[-1] if padded else 0
    if not 1 <= pad <= 16 or padded[-pad:] != bytes((pad,)) * pad:
        raise BullBleError("invalid AES-CBC padding in BLE frame")
    clear = padded[:-pad]
    if len(clear) != clear_length:
        raise BullBleError("BLE frame clear-length mismatch")
    return clear


class _Reassembler:
    def __init__(self) -> None:
        self._parts: dict[tuple[int, int, bool], dict[int, bytes]] = {}
        self._last: dict[tuple[int, int, bool], int] = {}

    def add(
        self,
        message_id: int,
        command: int,
        encrypted: bool,
        index: int,
        last: int,
        payload: bytes,
    ) -> bytes | None:
        key = (message_id, command, encrypted)
        if not 0 <= index <= last <= 15:
            raise BullBleError("invalid BLE fragment index")
        if index == 0 or key not in self._parts:
            self._parts[key] = {}
            self._last[key] = last
        elif self._last[key] != last:
            self._parts.pop(key, None)
            self._last.pop(key, None)
            raise BullBleError("inconsistent BLE fragment count")
        parts = self._parts[key]
        parts[index] = payload
        if len(parts) != last + 1 or any(part not in parts for part in range(last + 1)):
            return None
        result = b"".join(parts[index] for index in range(last + 1))
        self._parts.pop(key, None)
        self._last.pop(key, None)
        return result


class BullBleSession:
    """Protocol state for one externally connected D3-B32EB BLE client."""

    def __init__(
        self,
        client: BleakClient,
        identity: BleIdentity,
        confirm_random: Callable[[int, str], Awaitable[str]],
        confirm_device: Callable[[str, int, str, bytes], Awaitable[str]],
        event_callback: Callable[[BusinessMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._identity = identity
        self._confirm_random = confirm_random
        self._confirm_device = confirm_device
        self._event_callback = event_callback
        self._write: BleakGATTCharacteristic | None = None
        self._response = True
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._reassembler = _Reassembler()
        self._message_id = 0
        self._key: bytes | None = None
        self._iv: bytes | None = None
        self._pending: dict[int, _PendingRequest] = {}
        self._request_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None

    def _next_id(self) -> int:
        self._message_id = self._message_id % 15 + 1
        return self._message_id

    def _notification(self, _sender: BleakGATTCharacteristic, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    async def async_start(self) -> None:
        """Subscribe, authenticate, and start the lifetime notification reader."""
        if not self._client.is_connected:
            raise BullBleError("BLE client disconnected before session setup")

        services = self._client.services
        if services.get_service(SERVICE_UUID) is None:
            raise BullBleError("device does not expose Gongniu FEB3 service")
        characteristic = services.get_characteristic(WRITE_UUID)
        if characteristic is not None and "write" in characteristic.properties:
            self._write, self._response = characteristic, True
        else:
            characteristic = services.get_characteristic(WRITE_NO_RESPONSE_UUID)
            if characteristic is None:
                raise BullBleError(
                    "device has no supported Gongniu write characteristic"
                )
            self._write, self._response = characteristic, False

        subscribed = False
        for uuid, capability in (
            (NOTIFY_UUID, "notify"),
            (INDICATE_UUID, "indicate"),
        ):
            characteristic = services.get_characteristic(uuid)
            if characteristic is not None and capability in characteristic.properties:
                await self._client.start_notify(characteristic, self._notification)
                subscribed = True
        if not subscribed:
            raise BullBleError("device has no Gongniu notification characteristic")

        await self._authenticate()
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name=f"Bull BLE reader {self._identity.dn}"
        )

    async def async_stop(self) -> None:
        """Stop the notification reader without owning the GATT connection."""
        task = self._reader_task
        self._reader_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.debug("BLE reader stopped with an error", exc_info=True)

    def disconnected(self) -> None:
        """Unblock pending requests after the GATT disconnected callback fires."""
        task = self._reader_task
        if task and not task.done():
            task.cancel()

    async def async_wait_stopped(self) -> None:
        """Wait until the lifetime reader fails or is cancelled."""
        task = self._reader_task
        if task is None:
            raise BullBleError("BLE notification reader is not running")
        await asyncio.shield(task)

    async def _send_plain(self, command: int, payload: bytes) -> int:
        write = self._write
        if write is None:
            raise BullBleError("BLE write characteristic is not initialized")
        message_id = self._next_id()
        frame = bytes((message_id, command, 0, len(payload))) + payload
        await self._client.write_gatt_char(write, frame, response=self._response)
        return message_id

    async def _send_encrypted(self, command: int, payload: bytes) -> int:
        if self._key is None or self._iv is None:
            raise BullBleError("BLE session has not authenticated")
        message_id = self._next_id()
        await self._write_encrypted(message_id, command, payload)
        return message_id

    async def _write_encrypted(
        self, message_id: int, command: int, payload: bytes
    ) -> None:
        if self._key is None or self._iv is None:
            raise BullBleError("BLE session has not authenticated")
        write = self._write
        if write is None:
            raise BullBleError("BLE write characteristic is not initialized")
        cipher = _encrypt(payload, self._key, self._iv)
        frame = bytes((0x10 | message_id, command, 0, len(payload))) + cipher
        await self._client.write_gatt_char(write, frame, response=self._response)

    async def _next(self, timeout: float | None) -> tuple[int, int, bytes]:
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            try:
                if deadline is None:
                    raw = await self._queue.get()
                else:
                    raw = await asyncio.wait_for(
                        self._queue.get(), max(0.1, deadline - loop.time())
                    )
            except asyncio.TimeoutError as error:
                raise BullBleError("timed out waiting for BLE response") from error
            if len(raw) < 4:
                raise BullBleError("short BLE frame")
            message_id, command, fragment, clear_length = (
                raw[0] & 0x0F,
                raw[1],
                raw[2],
                raw[3],
            )
            encrypted = bool(raw[0] & 0x10) and command not in (0x20, 0x21)
            payload = raw[4:]
            if encrypted:
                if self._key is None or self._iv is None:
                    raise BullBleError("received encrypted frame before authentication")
                payload = _decrypt(payload, clear_length, self._key, self._iv)
            assembled = self._reassembler.add(
                message_id,
                command,
                encrypted,
                fragment & 0x0F,
                fragment >> 4,
                payload,
            )
            if assembled is not None:
                return message_id, command, assembled

    async def _reader_loop(self) -> None:
        """Continuously dispatch responses and unsolicited business events."""
        try:
            while True:
                message_id, command, payload = await self._next(None)
                message = self._business(command, message_id, payload)
                if command in (CMD_EVENT, CMD_BUBBLING_EVENT):
                    if self._event_callback:
                        try:
                            await self._event_callback(message)
                        except Exception:
                            _LOGGER.exception("Failed to apply a Bull BLE event")
                    continue

                pending = self._pending.get(message_id)
                if command == CMD_RESPONSE and pending and not pending.future.done():
                    if (
                        message.opcode != RESPONSE_OPCODE
                        or message.transaction_id != pending.transaction_id
                        or message.attr_type != pending.attr_type
                    ):
                        _LOGGER.debug(
                            "Ignored mismatched BLE response message_id=%d: "
                            "opcode=%s transaction_id=%s attr_type=%s; "
                            "expected opcode=0x%02x transaction_id=%d attr_type=0x%04x",
                            message_id,
                            (
                                f"0x{message.opcode:02x}"
                                if message.opcode is not None
                                else None
                            ),
                            message.transaction_id,
                            (
                                f"0x{message.attr_type:04x}"
                                if message.attr_type is not None
                                else None
                            ),
                            RESPONSE_OPCODE,
                            pending.transaction_id,
                            pending.attr_type,
                        )
                        continue
                    pending.future.set_result(message)
                    continue

                _LOGGER.debug(
                    "Ignored unsolicited BLE command 0x%02x message_id=%d",
                    command,
                    message_id,
                )
        finally:
            error = BullBleError("BLE notification reader stopped")
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(error)

    async def _wait_command(self, command: int, timeout: float = 12) -> bytes:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            message_id, received_command, payload = await self._next(
                max(0.1, deadline - asyncio.get_running_loop().time())
            )
            if received_command == command:
                return payload
            _LOGGER.debug(
                "ignored BLE command 0x%02x while waiting for 0x%02x",
                received_command,
                command,
            )

    async def _authenticate(self) -> None:
        random_value = await self._confirm_random(self._identity.pid, self._identity.dn)
        if len(random_value.encode()) != 16:
            raise BullBleError("cloud random is not a 16-byte IV")
        await self._send_plain(CMD_AUTH_RANDOM, random_value.encode())
        cipher = await self._wait_command(CMD_AUTH_CIPHER)
        key_hex = await self._confirm_device(
            random_value, self._identity.pid, self._identity.dn, cipher
        )
        self._key, self._iv = bytes.fromhex(key_hex), random_value.encode()
        await self._send_plain(CMD_AUTH_RESULT, b"\x00")
        await self._wait_command(CMD_AUTH_DONE)

    @staticmethod
    def _business(command: int, message_id: int, payload: bytes) -> BusinessMessage:
        if len(payload) < 7 or payload[0] != 0x41:
            return BusinessMessage(command, message_id, None, None, None, payload)
        return BusinessMessage(
            command,
            message_id,
            payload[1],
            payload[4],
            int.from_bytes(payload[5:7], "little"),
            payload[7:],
        )

    async def request(
        self, attr_type: int, value: bytes | None = None
    ) -> BusinessMessage:
        async with self._request_lock:
            if not self._client.is_connected:
                raise BullBleError("BLE client is disconnected")
            if self._reader_task is None or self._reader_task.done():
                raise BullBleError("BLE notification reader is not running")

            transaction_id = self._next_id()
            opcode = 0xD0 if value is None else 0xD1
            inner = (
                b"\x41"
                + bytes((opcode,))
                + b"\x12\x07"
                + bytes((transaction_id,))
                + attr_type.to_bytes(2, "little")
                + (value or b"")
            )
            message_id = self._next_id()
            future = asyncio.get_running_loop().create_future()
            self._pending[message_id] = _PendingRequest(
                transaction_id, attr_type, future
            )
            try:
                await self._write_encrypted(message_id, CMD_REQUEST, inner)
                return await asyncio.wait_for(asyncio.shield(future), 12)
            except asyncio.TimeoutError as error:
                raise BullBleError("timed out waiting for BLE response") from error
            finally:
                self._pending.pop(message_id, None)
                if not future.done():
                    future.cancel()

    async def send_service(self, attr_type: int) -> None:
        """Transmit an empty-input D1 service without assuming a business ACK.

        The D3 firmware seen in the field emits state events for start/stop,
        but may omit the matching ``0x03`` response.  Callers therefore
        confirm the resulting WorkState instead of treating that omission as
        a failed command.
        """
        transaction_id = self._next_id()
        inner = (
            b"\x41\xd1\x12\x07"
            + bytes((transaction_id,))
            + attr_type.to_bytes(2, "little")
        )
        await self._send_encrypted(CMD_REQUEST, inner)
