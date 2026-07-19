"""Local FEB3 BLE transport for the Bull D3-B32EB charger.

The transport deliberately accepts a ``BLEDevice`` supplied by Home
Assistant's bluetooth integration.  It must never start its own scanner: that
is what makes local adapters and ESPHome Bluetooth Proxies interchangeable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from bleak import BleakClient
from bleak.backends.device import BLEDevice
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
    attr_type: int | None
    value: bytes


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
        parts = self._parts.setdefault(key, {})
        parts[index] = payload
        self._last[key] = last
        if len(parts) != last + 1 or any(part not in parts for part in range(last + 1)):
            return None
        result = b"".join(parts[index] for index in range(last + 1))
        self._parts.pop(key, None)
        self._last.pop(key, None)
        return result


class BullBleSession:
    """One connected, cloud-authenticated local D3-B32EB session."""

    def __init__(
        self,
        device: BLEDevice,
        identity: BleIdentity,
        confirm_random: Callable[[int, str], Awaitable[str]],
        confirm_device: Callable[[str, int, str, bytes], Awaitable[str]],
        event_callback: Callable[[BusinessMessage], Awaitable[None]] | None = None,
    ) -> None:
        self._device = device
        self._identity = identity
        self._confirm_random = confirm_random
        self._confirm_device = confirm_device
        self._event_callback = event_callback
        self._client = BleakClient(device, timeout=15)
        self._write = None
        self._response = True
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._reassembler = _Reassembler()
        self._message_id = 0
        self._key: bytes | None = None
        self._iv: bytes | None = None

    def _next_id(self) -> int:
        self._message_id = self._message_id % 15 + 1
        return self._message_id

    def _notification(self, _sender: int, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    async def __aenter__(self) -> "BullBleSession":
        try:
            await self._client.connect()
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
                if (
                    characteristic is not None
                    and capability in characteristic.properties
                ):
                    await self._client.start_notify(characteristic, self._notification)
                    subscribed = True
            if not subscribed:
                raise BullBleError("device has no Gongniu notification characteristic")
            await self._authenticate()
            return self
        except Exception:
            if self._client.is_connected:
                try:
                    await self._client.disconnect()
                except Exception:  # pragma: no cover - depends on BLE backend
                    _LOGGER.debug("BLE cleanup after setup failure failed", exc_info=True)
            raise

    async def __aexit__(self, *_exc: object) -> None:
        if self._client.is_connected:
            await self._client.disconnect()

    async def _send_plain(self, command: int, payload: bytes) -> int:
        message_id = self._next_id()
        frame = bytes((message_id, command, 0, len(payload))) + payload
        await self._client.write_gatt_char(self._write, frame, response=self._response)
        return message_id

    async def _send_encrypted(self, command: int, payload: bytes) -> int:
        if self._key is None or self._iv is None:
            raise BullBleError("BLE session has not authenticated")
        message_id = self._next_id()
        cipher = _encrypt(payload, self._key, self._iv)
        frame = bytes((0x10 | message_id, command, 0, len(payload))) + cipher
        await self._client.write_gatt_char(self._write, frame, response=self._response)
        return message_id

    async def _next(self, timeout: float) -> tuple[int, int, bytes]:
        try:
            raw = await asyncio.wait_for(self._queue.get(), timeout)
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
            message_id, command, encrypted, fragment & 0x0F, fragment >> 4, payload
        )
        if assembled is None:
            return await self._next(timeout)
        return message_id, command, assembled

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
            return BusinessMessage(command, message_id, None, None, payload)
        return BusinessMessage(
            command,
            message_id,
            payload[1],
            int.from_bytes(payload[5:7], "little"),
            payload[7:],
        )

    async def request(
        self, attr_type: int, value: bytes | None = None
    ) -> BusinessMessage:
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
        message_id = await self._send_encrypted(CMD_REQUEST, inner)
        deadline = asyncio.get_running_loop().time() + 12
        while True:
            outer_id, command, payload = await self._next(
                max(0.1, deadline - asyncio.get_running_loop().time())
            )
            message = self._business(command, outer_id, payload)
            if command in (CMD_EVENT, CMD_BUBBLING_EVENT):
                if self._event_callback:
                    await self._event_callback(message)
                continue
            if command == CMD_RESPONSE and outer_id == message_id:
                return message

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
