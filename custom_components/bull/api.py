"""API interactions for bull-iot integration."""

import asyncio
from datetime import datetime, timezone
import uuid
import hmac
import base64
from hashlib import md5, sha256
from urllib.parse import parse_qsl, urlencode, urljoin
from functools import wraps
import json
import logging
import os

from aiohttp import ClientError, ClientTimeout, ClientSession
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import paho.mqtt.client as mqtt

from .const import (
    APPKEY,
    APPSECRET,
    APP_VERSION,
    APP_CLIENT_ID,
    APP_CLIENT_SECRET,
    API_URL,
    LOGIN_AES_KEY,
    SWITCH_PRODUCT_ID,
    COVER_PRODUCT_ID,
    CHARGER_PRODUCT_ID,
)
from .ble import BleIdentity, BullBleError

_LOGGER = logging.getLogger(__name__)


class InvalidTokenError(Exception):
    """Exception raised for invalid token."""


class LoginRequiredError(Exception):
    """Exception raised for login required."""


class NetworkError(HomeAssistantError):
    """Exception raised for network connection error."""


class AuthenticationError(HomeAssistantError):
    """Exception raised when stored account credentials are rejected."""

    def __init__(self, error_key: str) -> None:
        super().__init__(error_key)
        self.error_key = error_key


class CloudApiError(HomeAssistantError):
    """Exception raised when MosHome rejects an otherwise valid request."""

    def __init__(self, operation: str, response: dict) -> None:
        self.operation = operation
        self.code = response.get("code")
        self.message = response.get("message") or "unknown cloud error"
        super().__init__(
            f"{operation} rejected by MosHome: code={self.code}, "
            f"message={self.message!r}"
        )


def retry(func):
    """Retry once after token recovery or a transient network failure."""

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        stale_access_token = self.access_token
        for attempt in range(2):
            try:
                return await func(self, *args, **kwargs)
            except InvalidTokenError as err:
                if attempt:
                    self._start_reauth()
                    raise AuthenticationError("invalid_auth") from err
                await self.async_refresh_access_token(stale_access_token)
            except LoginRequiredError as err:
                if attempt:
                    self._start_reauth()
                    raise AuthenticationError("invalid_auth") from err
                await self.async_login(
                    self.username,
                    self.password,
                    stale_access_token=stale_access_token,
                )
            except NetworkError:
                if attempt:
                    raise
                await asyncio.sleep(0.5)

        raise RuntimeError("unreachable")

    return wrapper


class BullDevice:
    """A class to represent a Bull IoT device, binds to iotId.
    In some cases, a single device may contain multiple switches.
    They share the same BullDevice object but have different identifiers."""

    def __init__(self, cloud, info) -> None:
        self._cloud = cloud
        self.iot_id = info["iotId"]
        self.global_product_id = info["product"]["globalProductId"]
        self.nick_name = ""
        self.product_name = ""
        self.model_name = ""
        self.firmware_version = ""
        self.room = info.get("roomName", "")
        # Key is identifier, value is int, float or string
        # int 1 / 0 (indicating switch on / off etc.)
        # float (indicating socket power etc.)
        # string (indication device online etc.)
        self.identifier_values = {}
        self.raw_info = {}
        self.raw_device_info = {}
        self._last_status_time = None
        self._connectivity_entity = None
        self.ble_charger = None
        self.ble_available = False

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        # A supported BLE charger deliberately has no cloud-control fallback:
        # its entities are available only while the authenticated GATT owner is
        # up. Other product types retain their cloud/MQTT availability.
        if self.ble_charger is not None:
            return self.ble_available
        # status is ONLINE or OFFLINE from /v2/home/devices API
        # It may change to int from thing.status mqtt message
        # 1 - Online, 3 - Offline
        return self.ble_available or self.identifier_values.get("status") in [
            "ONLINE",
            1,
        ]

    async def set_dp(self, identifier: str, prop: int):
        await self._cloud.set_property(self.iot_id, identifier, prop)

    async def invoke_thing_service(self, identifier: str):
        await self._cloud.invoke_thing_service(self.iot_id, identifier)

    def _schedule_availability_update(self) -> None:
        """Schedule state updates for entities whose availability may change."""
        if self._connectivity_entity:
            self._connectivity_entity.schedule_update_ha_state()

    def set_ble_available(self, available: bool) -> None:
        """Publish a BLE availability change to every entity for this device."""
        available = bool(available)
        if self.ble_available == available:
            return

        self.ble_available = available
        self._schedule_availability_update()
        _LOGGER.debug("Update device BLE availability: %s %s", self.iot_id, available)

    def _sync_raw_info_value(self, identifier: str, value) -> None:
        """Sync incremental MQTT value into raw_info property snapshot."""
        raw_info = getattr(self, "raw_info", None)
        if not isinstance(raw_info, dict):
            return

        properties = raw_info.get("property")
        if not isinstance(properties, dict):
            return

        if identifier == "status":
            raw_info["status"] = value
            return

        if "." not in identifier:
            prop_entry = properties.get(identifier)
            if isinstance(prop_entry, dict):
                prop_entry["value"] = value
            return

        parent_identifier, child_identifier = identifier.split(".", 1)
        parent_entry = properties.get(parent_identifier)
        if not isinstance(parent_entry, dict):
            return

        parent_value = parent_entry.get("value")
        if isinstance(parent_value, dict):
            parent_value[child_identifier] = value

    def update_dp(self, identifier: str, prop):
        self.identifier_values[identifier] = prop
        self._sync_raw_info_value(identifier, prop)
        if self._connectivity_entity:
            self._connectivity_entity.schedule_update_ha_state()


class BullSwitch(BullDevice):
    """A class to represent a Bull IoT switch device."""

    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        # For switches, the identifiers may contain PowerSwitch, PowerSwitch_1, PowerSwitch_2, PowerSwitch_3
        # Key is identifier, value is name (e.g. "客厅吊灯")
        self.identifier_names = {}
        # Key is identifier, value is entity
        self._entities = {}
        self._button_entities = []

    def _schedule_availability_update(self) -> None:
        """Schedule state updates for all switch, sensor, and button entities."""
        entities = [
            *self._entities.values(),
            *self._button_entities,
            self._connectivity_entity,
        ]
        scheduled_ids = set()
        for entity in entities:
            if entity is None or id(entity) in scheduled_ids:
                continue
            scheduled_ids.add(id(entity))
            entity.schedule_update_ha_state()

    def update_dp(self, identifier: str, prop):
        self.identifier_values[identifier] = prop
        self._sync_raw_info_value(identifier, prop)
        entity = self._entities.get(identifier)
        if entity:
            entity.schedule_update_ha_state()
        for button_entity in self._button_entities:
            button_entity.schedule_update_ha_state()
        if self._connectivity_entity:
            self._connectivity_entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s", self.iot_id, identifier, prop)


class BullCover(BullDevice):
    """A class to represent a Bull IoT cover device."""

    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        self.name = None
        self._entity = None

    def update_dp(self, identifier: str, prop):
        self.identifier_values[identifier] = prop
        self._sync_raw_info_value(identifier, prop)
        entity = self._entity
        if entity:
            entity.schedule_update_ha_state()
        if self._connectivity_entity:
            self._connectivity_entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s", self.iot_id, identifier, prop)


class BullApi:
    """A class to represent the Bull IoT API."""

    def __init__(self, hass=None, data: dict | None = None, entry=None) -> None:
        self._hass = hass
        self._entry = entry
        if data:
            self.deserialize(data)
        else:
            self.username = None
            self.password = None
            self.selected_families = []
        self.access_token = None
        self.refresh_token = None
        self.openid: str = ""
        self.device_list = {}
        self.families = []
        self.client = None
        self._destroyed = False
        self._reauth_started = False
        self._mqtt_auth_recovery_pending = False
        self._token_lock = asyncio.Lock()
        self._mqtt_lock = asyncio.Lock()
        self._owns_session = not self._hass
        if self._hass:
            self.session = async_create_clientsession(self._hass)
        else:
            self.session = ClientSession()
        self._request_timeout = ClientTimeout(total=10)

    async def setup(self) -> None:
        """Set up the Bull IoT API."""
        self._destroyed = False
        await self.async_login(self.username, self.password)
        await self.async_get_all_devices_list_mos()
        await self.async_setup_ble_chargers()
        self.init_mqtt()
        _LOGGER.info("BullApi started")

    async def async_destroy(self) -> None:
        """Destroy the Bull IoT API."""
        self._destroyed = True
        await self.async_stop_mqtt()
        chargers = []
        for device in self.device_list.values():
            charger = device.ble_charger
            if charger:
                device.ble_charger = None
                chargers.append(charger)
        if chargers:
            await asyncio.gather(*(charger.async_stop() for charger in chargers))
        if self._owns_session and not self.session.closed:
            await self.session.close()
        _LOGGER.info("BullApi stopped")

    def serialize(self):
        """Serialize the Bull IoT API."""
        return {
            "username": self.username,
            "password": self.password,
            "selected_families": self.selected_families,
        }

    def deserialize(self, data: dict) -> None:
        """Deserialize the Bull IoT API."""
        self.username = data.get("username")
        self.password = data.get("password")
        self.selected_families = data.get("selected_families") or []

    def select_family(self, selected_families):
        """Select the families to load devices."""
        self.selected_families = selected_families

    @staticmethod
    def _require_success(response: dict, operation: str) -> dict:
        """Return a successful response or raise a useful cloud exception."""
        if isinstance(response, dict) and (
            response.get("success") is True
            or ("success" not in response and response.get("code") == 200)
        ):
            return response
        raise CloudApiError(operation, response if isinstance(response, dict) else {})

    async def _async_login(self, username: str, password: str) -> None:
        """Perform one login without acquiring the token recovery lock."""
        form_params = {
            "username": self.encrypt_sensitive_field(username),
            # The React Native layer derives this before calling the Android
            # network wrapper; the wrapper itself sends it unchanged.
            "password": self.encrypt_sha256(
                self.encrypt_sha256(password) + self.encrypt_sha256("GONGNIU")
            ),
        }
        try:
            res = await self.async_make_request(
                "POST",
                "/mos/uic/v2/auth/form",
                "application/x-www-form-urlencoded; charset=utf-8",
                {"Login_parameter": "APP_PWD"},
                urlencode(form_params),
                form_params=form_params,
            )
        except (InvalidTokenError, LoginRequiredError) as err:
            raise AuthenticationError("invalid_auth") from err

        if not res["success"]:
            # MosHome v2 intentionally returns one code for either credential,
            # so do not imply which field was wrong.
            if res["code"] == 901006:
                raise AuthenticationError("invalid_auth")
            if res["code"] == 901001:
                raise AuthenticationError("wrong_user")
            if res["code"] == 901015:
                raise AuthenticationError("wrong_pwd")
            raise CloudApiError("login", res)

        self.username = username
        self.password = password
        self.access_token = res["result"]["access_token"]
        self.refresh_token = res["result"]["refresh_token"]
        self.openid = str(res["result"]["openid"])

    async def async_login(
        self,
        username: str,
        password: str,
        *,
        stale_access_token: str | None = None,
    ) -> None:
        """Log in once, coalescing concurrent recovery attempts."""
        async with self._token_lock:
            if (
                stale_access_token is not None
                and self.access_token != stale_access_token
            ):
                return
            try:
                await self._async_login(username, password)
            except AuthenticationError:
                if stale_access_token is not None:
                    self._start_reauth()
                raise
            await self.async_restart_mqtt()

    @staticmethod
    def encrypt_sha256(data):
        """Encrypt data with SHA256."""
        hash_obj = sha256()
        hash_obj.update(data.encode("utf-8"))
        return hash_obj.hexdigest()

    @staticmethod
    def encrypt_sensitive_field(value: str) -> str:
        """Match MosHome's AES-CBC encryption for login usernames.

        The Android client uses a fresh 16-byte IV per request, prefixes it to
        the ciphertext, and sends the result as upper-case hexadecimal.
        """
        value = (value or "").strip()
        if not value:
            return value

        plain = value.encode("utf-8")
        pad_length = 16 - len(plain) % 16
        padded = plain + bytes([pad_length]) * pad_length
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(base64.b64decode(LOGIN_AES_KEY)), modes.CBC(iv))
        encryptor = cipher.encryptor()
        return (iv + encryptor.update(padded) + encryptor.finalize()).hex().upper()

    async def async_refresh_access_token(
        self, stale_access_token: str | None = None
    ) -> None:
        """Refresh once and rebuild MQTT so all transports use the new token."""
        async with self._token_lock:
            if (
                stale_access_token is not None
                and self.access_token != stale_access_token
            ):
                return

            payload = json.dumps(
                {
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                separators=(",", ":"),
            )
            try:
                res = await self.async_make_request(
                    "POST",
                    "/mos/uic/v1/auth/token",
                    "application/json; charset=utf-8",
                    {},
                    payload,
                )
            except (InvalidTokenError, LoginRequiredError):
                try:
                    await self._async_login(self.username, self.password)
                except AuthenticationError:
                    self._start_reauth()
                    raise
            else:
                self._require_success(res, "refresh access token")
                self.access_token = res["result"]["access_token"]
                self.refresh_token = res["result"]["refresh_token"]

            await self.async_restart_mqtt()

    def _start_reauth(self) -> None:
        """Ask Home Assistant for new credentials once per API lifetime."""
        if self._entry is None or self._hass is None or self._reauth_started:
            return
        self._reauth_started = True
        self._entry.async_start_reauth(self._hass)

    @retry
    async def async_get_families(self) -> None:
        """Obtain the list of families associated to a user."""
        res = await self.async_make_request(
            "GET",
            "/v2/families",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "",
        )
        self._require_success(res, "get families")
        self.families = res["result"]

    @retry
    async def async_switch_family(self, family_id: int) -> None:
        """Switch the family associated to a user."""
        res = await self.async_make_request(
            "POST",
            f"/v1/families/{family_id}/switch",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "{}",
        )
        self._require_success(res, f"switch family {family_id}")

    @retry
    async def async_get_devices_list(self) -> None:
        """Obtain the list of devices associated to a user.
        This API will only load devices from the family that the user last visited.
        If the user has multiple families (for example, shared by other users), then not all devices can be loaded.
        """
        res = await self.async_make_request(
            "GET",
            "/v2/home/devices",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "",
        )
        self._require_success(res, "get devices")
        await self.async_parse_devices(res)

    async def async_get_all_devices_list(self) -> None:
        """Obtain the list of all devices associated to a user.
        It will switch family and load device list based on user configuration.
        """
        # Support old configuration: no selected_families given
        if not self.selected_families:
            await self.async_get_families()
            self.selected_families = [family["familyId"] for family in self.families]

        for family_id in self.selected_families:
            await self.async_switch_family(family_id)
            await self.async_get_devices_list()

    @retry
    async def async_get_device_info(self, iot_id: str) -> dict:
        """Obtain the device information."""
        res = await self.async_make_request(
            "GET",
            f"/mos/device/v1/deviceInfo/{iot_id}/get",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "",
        )
        self._require_success(res, f"get device info {iot_id}")
        return res["result"]

    @retry
    async def async_confirm_ble_random(
        self,
        pid: int | str,
        device_name: str,
        *,
        use_user_token: bool = True,
        endpoint: str = "/mos/ble/v1/confirmRandom",
    ) -> str:
        """Request the BLE authentication challenge for a device."""
        headers = {}
        if use_user_token and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        response = await self.async_make_request(
            "POST",
            endpoint,
            "application/json",
            headers,
            json.dumps(
                {"pid": str(pid), "dn": device_name},
                separators=(",", ":"),
            ),
        )
        if not response.get("success"):
            raise BullBleError(
                f"confirmRandom rejected: code={response.get('code')}, message={response.get('message')!r}"
            )
        result = response.get("result") if isinstance(response, dict) else None
        random_value = result.get("random") if isinstance(result, dict) else result
        if not isinstance(random_value, str) or len(random_value.encode()) != 16:
            raise BullBleError("cloud did not return a 16-byte BLE random")
        return random_value

    @retry
    async def async_confirm_ble_device(
        self, random_value: str, pid: int, device_name: str, cipher: bytes
    ) -> str:
        """Exchange the device cipher for this session's AES key."""
        response = await self.async_make_request(
            "POST",
            "/mos/ble/v1/confirmDevice",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            json.dumps(
                {
                    "random": random_value,
                    "pid": str(pid),
                    "dn": device_name,
                    "cipher": cipher.hex().upper(),
                },
                separators=(",", ":"),
            ),
        )
        if not response.get("success"):
            raise BullBleError(
                f"confirmDevice rejected: code={response.get('code')}, message={response.get('message')!r}"
            )
        result = response.get("result") if isinstance(response, dict) else None
        key = result.get("bleKey") if isinstance(result, dict) else result
        if not isinstance(key, str) or len(key) != 32:
            raise BullBleError("cloud did not return a 16-byte BLE AES key")
        try:
            bytes.fromhex(key)
        except ValueError as error:
            raise BullBleError("cloud returned a non-hex BLE AES key") from error
        return key

    @staticmethod
    def _ble_identity(info: dict) -> BleIdentity | None:
        """Extract the D3 cloud identity; only PID 309 is supported locally."""
        product = info.get("product") if isinstance(info.get("product"), dict) else {}
        pid = product.get("globalProductId", info.get("pid"))
        dn = info.get("deviceName", info.get("dn"))
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        if pid != 309 or not isinstance(dn, str) or len(dn) != 12:
            return None
        return BleIdentity(pid, dn.upper())

    async def async_setup_ble_chargers(self) -> None:
        """Register BLE discovery for supported chargers in this account."""
        if not self._hass:
            return
        from .ble_charger import BullBleCharger

        for device in self.device_list.values():
            identity = self._ble_identity(device.raw_info) or self._ble_identity(
                device.raw_device_info
            )
            if not identity or device.ble_charger:
                continue
            device.ble_charger = BullBleCharger(
                self._hass,
                device,
                identity,
                self.async_confirm_ble_random,
                self.async_confirm_ble_device,
            )
            await device.ble_charger.async_start()
            _LOGGER.info(
                "Registered local BLE charger %s (PID %s)", device.iot_id, identity.pid
            )

    async def async_parse_device(self, info: dict) -> None:
        """Parse the device information."""
        iot_id = info["iotId"]
        global_product_id = info["product"]["globalProductId"]
        element_identifier = info.get("elementIdentifier", "")
        is_device_entity = info.get("deviceEntity", False)

        # 1. Get or create device object
        if self.device_list.get(iot_id):
            device = self.device_list[iot_id]
        else:
            if global_product_id in SWITCH_PRODUCT_ID | CHARGER_PRODUCT_ID:
                device = BullSwitch(self, info)
            elif global_product_id in COVER_PRODUCT_ID:
                device = BullCover(self, info)
            else:
                device = BullDevice(self, info)
            await self.async_add_new_device(device, info)

        # 2. Handle device name (prefer nickName from deviceEntity: true)
        if is_device_entity:
            device.nick_name = info.get("nickName", device.nick_name)
            return  # Do not create entity for the main device entry

        # 3. Handle functional entities
        if global_product_id in SWITCH_PRODUCT_ID | CHARGER_PRODUCT_ID:
            if element_identifier:
                device.identifier_names[element_identifier] = info.get(
                    "nickName", element_identifier
                )
        elif global_product_id in COVER_PRODUCT_ID:
            device.name = info.get("nickName", device.nick_name)
        else:
            _LOGGER.info(
                "Unknown product %s, keep connectivity entity only: %s %s %s",
                global_product_id,
                device.iot_id,
                device.product_name,
                device.model_name,
            )

    async def async_add_new_device(self, device: BullDevice, info: dict) -> None:
        """Add a new device to the device list."""
        self.device_list[device.iot_id] = device
        device.raw_info = info
        for prop in info["property"].values():
            key = prop["identifier"]
            for flattened_key, flattened_value in self._flatten_identifier_values(
                key, prop["value"]
            ).items():
                device.identifier_values[flattened_key] = flattened_value
        device_info = await self.async_get_device_info(device.iot_id)
        device.raw_device_info = device_info
        device.product_name = device_info["productName"]
        device.model_name = device_info["modelName"]
        device.firmware_version = device_info["firmwareVersion"]
        # Use productName as the default nick_name (reliable fallback)
        device.nick_name = device.product_name

    def _flatten_identifier_values(self, identifier: str, value) -> dict:
        """Return flat identifier-value mapping for legacy and nested sensor mapping."""
        flattened = {identifier: value}
        if isinstance(value, dict):
            for child_identifier, child_value in value.items():
                flattened[f"{identifier}.{child_identifier}"] = child_value
        return flattened

    async def async_parse_devices(self, db) -> None:
        """Parse the devices information."""
        for info in db["result"]:
            await self.async_parse_device(info)
        if self._hass:
            self._hass.async_create_task(self.telemetry())

    @retry
    async def async_get_rooms_mos(self) -> None:
        """Obtain the list of rooms associated to a user.
        This API will only load devices from the family that the user last visited.
        If the user has multiple families (for example, shared by other users), then not all devices can be loaded.
        """
        res = await self.async_make_request(
            "GET",
            "/mos/home/v3/rooms",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "",
        )
        self._require_success(res, "get rooms and devices")
        await self.async_parse_devices_mos(res)

    async def async_get_all_devices_list_mos(self) -> None:
        """Obtain the list of all devices associated to a user.
        It will switch family and load device list based on user configuration.
        """
        # Support old configuration: no selected_families given
        if not self.selected_families:
            await self.async_get_families()
            self.selected_families = [family["familyId"] for family in self.families]

        for family_id in self.selected_families:
            await self.async_switch_family(family_id)
            await self.async_get_rooms_mos()

    async def async_parse_devices_mos(self, db) -> None:
        """Parse the devices information (MosHome)."""
        for info in db["result"]["devices"][0]["deviceList"]:
            await self.async_parse_device(info)
        if self._hass:
            self._hass.async_create_task(self.telemetry())

    async def telemetry(self) -> None:
        """Send telemetry data to the server."""
        url = "https://api.zsq.im/hass/"
        data = []
        for device in self.device_list.values():
            entry = {}
            entry["globalProductId"] = device.global_product_id
            entry["productName"] = device.product_name
            entry["modelName"] = device.model_name
            entry["firmwareVersion"] = device.firmware_version
            entry["property"] = list(device.identifier_values)
            data.append(entry)
        json_data = json.dumps(data)

        try:
            async with self.session.post(
                url,
                data=json_data,
                headers={"Content-Type": "application/json"},
                timeout=self._request_timeout,
            ) as response:
                await response.read()
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug("Telemetry request failed: %s", err)

    @staticmethod
    def _mqtt_reason_value(reason_code) -> int | None:
        """Return an integer for Paho v2 reason-code objects."""
        value = getattr(reason_code, "value", reason_code)
        return value if isinstance(value, int) else None

    def _mqtt_on_connect(
        self, client, userdata, flags, reason_code, properties=None
    ) -> None:
        """Bind the account after every successful MQTT connection."""
        _LOGGER.info("MQTT connected with result code: %s", reason_code)
        reason_value = self._mqtt_reason_value(reason_code)
        if reason_value != mqtt.MQTT_ERR_SUCCESS:
            if reason_value in (4, 5, 134, 135):
                self._schedule_mqtt_token_refresh()
            return

        subscribe_rc, _ = client.subscribe("/sys/app/down/account/bind_reply", qos=0)
        if subscribe_rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.warning("MQTT bind-reply subscription failed: rc=%d", subscribe_rc)

        client_id = "IOS@2.9.1@" + self.openid
        payload = {
            "id": "msg_id_bind_85",
            "params": {"token": self.access_token},
            "request": {"clientId": client_id, "userId": self.openid},
            "version": "1.0",
        }
        publish_info = client.publish("/sys/app/up/account/bind", json.dumps(payload))
        if publish_info.rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.warning("MQTT account bind publish failed: rc=%d", publish_info.rc)

    def _mqtt_on_disconnect(self, client, userdata, *callback_args) -> None:
        """Report an unexpected disconnect; Paho handles reconnect backoff."""
        # Paho Callback API v1 supplies either (rc) or (reason_code,
        # properties); v2 supplies (disconnect_flags, reason_code,
        # properties). Keep the handler compatible with either installed
        # version so the integration does not constrain Home Assistant's Paho.
        if len(callback_args) >= 3:
            reason_code = callback_args[1]
        elif callback_args:
            reason_code = callback_args[0]
        else:
            reason_code = None
        if client is self.client and not self._destroyed:
            _LOGGER.warning("MQTT disconnected: %s; reconnecting", reason_code)
        else:
            _LOGGER.debug("MQTT client stopped: %s", reason_code)

    def _mqtt_on_connect_fail(self, client, userdata) -> None:
        """Report asynchronous connection failures without stopping retries."""
        if client is self.client and not self._destroyed:
            _LOGGER.warning("MQTT connection failed; reconnecting with backoff")

    def _mqtt_on_message(self, client, userdata, msg) -> None:
        """Validate and dispatch one MQTT message without leaking exceptions."""
        try:
            db = json.loads(msg.payload)
            if not isinstance(db, dict):
                raise ValueError("top-level JSON value is not an object")

            if msg.topic.endswith("/account/bind_reply"):
                code = db.get("code")
                try:
                    code_value = int(code) if code is not None else None
                except (TypeError, ValueError):
                    code_value = None
                log = (
                    _LOGGER.info
                    if code is None or code_value in (0, 200)
                    else _LOGGER.warning
                )
                log(
                    "MQTT account bind reply: code=%s message=%s",
                    code,
                    db.get("message"),
                )
                if code_value in (9008, 901006) or db.get("error") == "invalid_token":
                    self._schedule_mqtt_token_refresh()
                return

            params = db.get("params")
            if not isinstance(params, dict):
                raise ValueError("params is missing or is not an object")
            iot_id = params.get("iotId")
            if not isinstance(iot_id, str) or not iot_id:
                raise ValueError("params.iotId is missing")

            if db.get("method") == "thing.properties":
                items = params.get("items")
                if not isinstance(items, dict):
                    raise ValueError("params.items is missing or is not an object")
                for identifier, info in items.items():
                    if not isinstance(identifier, str) or not isinstance(info, dict):
                        _LOGGER.warning(
                            "Ignoring malformed MQTT property for device %s", iot_id
                        )
                        continue
                    if "value" not in info:
                        _LOGGER.warning(
                            "Ignoring MQTT property without a value for device %s",
                            iot_id,
                        )
                        continue
                    for (
                        flattened_key,
                        flattened_value,
                    ) in self._flatten_identifier_values(
                        identifier, info["value"]
                    ).items():
                        self.on_message(iot_id, flattened_key, flattened_value)
                return

            if db.get("method") != "thing.status":
                return

            info = params.get("status")
            if not isinstance(info, dict) or "value" not in info:
                raise ValueError("params.status is missing or invalid")
            status_time = info.get("time")
            device = self.device_list.get(iot_id)
            if (
                device
                and isinstance(status_time, (int, float))
                and isinstance(device._last_status_time, (int, float))
                and status_time < device._last_status_time
            ):
                _LOGGER.debug(
                    "Ignore stale MQTT status: iot_id=%s status=%s "
                    "time=%s last_time=%s",
                    iot_id,
                    info["value"],
                    status_time,
                    device._last_status_time,
                )
                return
            if device and isinstance(status_time, (int, float)):
                device._last_status_time = status_time
            self.on_message(iot_id, "status", info["value"])
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            _LOGGER.warning("Ignoring malformed MQTT message on topic %s", msg.topic)
        except Exception:
            _LOGGER.exception("Unexpected error handling MQTT message on %s", msg.topic)

    def _schedule_mqtt_token_refresh(self) -> None:
        """Move token recovery from Paho's thread onto HA's event loop."""
        if self._hass is None or self._destroyed:
            return
        stale_access_token = self.access_token

        def create_task() -> None:
            if self._destroyed or self._mqtt_auth_recovery_pending:
                return
            self._mqtt_auth_recovery_pending = True
            self._hass.async_create_task(
                self._async_recover_mqtt_auth(stale_access_token)
            )

        self._hass.loop.call_soon_threadsafe(create_task)

    async def _async_recover_mqtt_auth(self, stale_access_token: str | None) -> None:
        """Refresh credentials after an MQTT authentication rejection."""
        try:
            await self.async_refresh_access_token(stale_access_token)
        except AuthenticationError:
            _LOGGER.error("MQTT authentication requires account reauthentication")
        except (CloudApiError, NetworkError):
            _LOGGER.warning("Could not refresh MQTT credentials", exc_info=True)
        except Exception:
            _LOGGER.exception("Unexpected error refreshing MQTT credentials")
        finally:
            self._mqtt_auth_recovery_pending = False

    def init_mqtt(self) -> None:
        """Initialize a Paho client with the current access token."""
        if self._destroyed:
            return
        client_id = "IOS@2.9.1@" + self.openid
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
            )
        else:
            client = mqtt.Client(client_id=client_id)
        client.suppress_exceptions = True
        client.on_connect = self._mqtt_on_connect
        client.on_disconnect = self._mqtt_on_disconnect
        client.on_connect_fail = self._mqtt_on_connect_fail
        client.on_message = self._mqtt_on_message
        client.reconnect_delay_set(min_delay=15, max_delay=120)
        client.username_pw_set(self.openid, self.access_token)
        self.client = client
        connect_rc = client.connect_async("emqx-prod.iotbull.com", port=1883)
        if connect_rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.warning("Could not start MQTT connection: rc=%d", connect_rc)
        client.loop_start()

    @staticmethod
    def _stop_mqtt_client(client) -> None:
        """Stop one Paho network thread; intended for an executor."""
        disconnect_rc = client.disconnect()
        if disconnect_rc not in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
            _LOGGER.debug("MQTT disconnect returned rc=%d", disconnect_rc)
        client.loop_stop()

    async def _async_stop_mqtt_locked(self) -> None:
        """Stop the current MQTT client while the caller holds the MQTT lock."""
        client = self.client
        self.client = None
        if client is None:
            return
        if self._hass:
            await self._hass.async_add_executor_job(self._stop_mqtt_client, client)
        else:
            await asyncio.to_thread(self._stop_mqtt_client, client)

    async def async_stop_mqtt(self) -> None:
        """Disconnect MQTT and join its network thread without blocking HA."""
        async with self._mqtt_lock:
            await self._async_stop_mqtt_locked()

    async def async_restart_mqtt(self) -> None:
        """Rebuild an active client so credentials and bind token stay in sync."""
        async with self._mqtt_lock:
            if self.client is None or self._destroyed:
                return
            await self._async_stop_mqtt_locked()
            if not self._destroyed:
                self.init_mqtt()

    def on_message(self, iot_id: str, identifier: str, value) -> None:
        """Handle the MQTT message."""
        device = self.device_list.get(iot_id)
        if device:
            device.update_dp(identifier, value)

    @retry
    async def set_property(self, iot_id: str, identifier: str, value: int) -> None:
        """Set the device property."""
        response = await self.async_make_request(
            "PUT",
            f"/mos/v1/dc/setDeviceProperty/{iot_id}",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            json.dumps([{"value": value, "identifier": identifier}]),
        )
        self._require_success(response, f"set property {identifier}")

    @retry
    async def invoke_thing_service(self, iot_id: str, identifier: str) -> None:
        """Invoke thing service by identifier."""
        response = await self.async_make_request(
            "PUT",
            f"/mos/iot/v1/devices/{iot_id}/invokeThingService",
            "application/json",
            {
                "Authorization": f"Bearer {self.access_token}",
            },
            json.dumps({"identifier": identifier}),
        )
        self._require_success(response, f"invoke service {identifier}")

    @staticmethod
    def _canonical_resource(path: str, form_params: dict[str, str] | None) -> str:
        """Build CloudAPI's path/query component of the string to sign."""
        if not form_params:
            return path

        query = "&".join(
            f"{key}={value}" if value else key
            for key, value in sorted(form_params.items())
        )
        return f"{path}?{query}" if query else path

    @staticmethod
    def _make_cloudapi_headers(
        method: str,
        path: str,
        content_type: str,
        form_params: dict[str, str] | None,
        body: str,
    ) -> dict[str, str]:
        """Generate the same CloudAPI HMAC headers as MosHome 5.1.13."""
        now = datetime.now(timezone.utc)
        date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
        x_ca_headers = {
            "x-ca-key": APPKEY,
            "x-ca-nonce": str(uuid.uuid4()),
            "x-ca-signature-method": "HmacSHA256",
            "x-ca-timestamp": str(int(now.timestamp() * 1000)),
        }
        canonical_headers = "".join(
            f"{key}:{value}\n" for key, value in sorted(x_ca_headers.items())
        )
        content_md5 = ""
        if body and not form_params:
            content_md5 = base64.b64encode(md5(body.encode("utf-8")).digest()).decode()
        payload = (
            f"{method}\napplication/json; charset=utf-8\n{content_md5}\n"
            f"{content_type}\n{date}\n"
            f"{canonical_headers}"
            f"{BullApi._canonical_resource(path, form_params)}"
        )
        signature = base64.b64encode(
            hmac.new(APPSECRET, payload.encode("utf-8"), digestmod=sha256).digest()
        ).decode()
        return {
            "Host": "api.iotbull.com",
            "X-Ca-Key": APPKEY,
            "X-Ca-Nonce": x_ca_headers["x-ca-nonce"],
            "X-Ca-Signature-Method": "HmacSHA256",
            "X-Ca-Timestamp": x_ca_headers["x-ca-timestamp"],
            "X-Ca-Signature-Headers": ",".join(sorted(x_ca_headers)),
            "X-Ca-Signature": signature,
            "CA_VERSION": "1",
            "Authorization": "Basic "
            + base64.b64encode(
                f"{APP_CLIENT_ID}:{APP_CLIENT_SECRET}".encode("utf-8")
            ).decode(),
            "X-App-Platform": "android",
            "X-App-Version": APP_VERSION,
            "User-Agent": "ALIYUN-ANDROID-DEMO",
            "Accept": "application/json; charset=utf-8",
            "Accept-Language": "zh-Hans;q=1, zh-Hant-CN;q=0.9, en-CN;q=0.8",
            "Accept-Encoding": "gzip",
            "Date": date,
            "Content-Type": content_type,
            **({"Content-Md5": content_md5} if content_md5 else {}),
        }

    async def async_make_request(
        self,
        method: str,
        path: str,
        content_type: str,
        header,
        body: str,
        *,
        form_params: dict[str, str] | None = None,
    ) -> dict:
        """Perform a request signed with the current MosHome CloudAPI scheme."""
        url = urljoin(API_URL, path)
        if form_params is None and content_type.startswith(
            "application/x-www-form-urlencoded"
        ):
            form_params = dict(parse_qsl(body, keep_blank_values=True))
        header = {
            **self._make_cloudapi_headers(
                method, path, content_type, form_params, body
            ),
            **header,
        }
        try:
            async with self.session.request(
                method,
                url,
                headers=header,
                data=body,
                timeout=self._request_timeout,
            ) as response:
                text = await response.text()
                try:
                    res = json.loads(text)
                except json.JSONDecodeError as err:
                    _LOGGER.error(
                        "Invalid JSON response for %s: HTTP %s",
                        path,
                        response.status,
                    )
                    raise NetworkError("invalid_response") from err
                if not isinstance(res, dict):
                    raise NetworkError("invalid_response")
                _LOGGER.debug(
                    "Request completed: path=%s status=%s success=%s code=%s "
                    "message=%r",
                    path,
                    response.status,
                    res.get("success"),
                    res.get("code"),
                    res.get("message"),
                )
        except (ClientError, TimeoutError) as err:
            _LOGGER.error("Request failed: %s %s", path, err)
            raise NetworkError("connection_failed") from err

        if not res.get("success"):
            if res.get("error") == "invalid_token":
                raise InvalidTokenError
            # {"code":9008,"message":"请重新登录","result":null,"success":false}
            if res.get("code") == 9008:
                raise LoginRequiredError

        return res
