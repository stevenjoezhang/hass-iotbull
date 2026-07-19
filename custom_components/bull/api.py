"""API interactions for bull-iot integration."""

from datetime import datetime, timezone
import uuid
import hmac
import base64
from hashlib import md5, sha256
from urllib.parse import parse_qsl, urlencode, urljoin
from functools import partial
import json
import logging
import os

from aiohttp import ClientError, ClientTimeout, ClientSession
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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


class NetworkError(Exception):
    """Exception raised for network connection error."""


def retry(func):
    """Retry decorator."""

    async def wrapper(self, *args, **kwargs):
        try:
            res = await func(self, *args, **kwargs)
            return res
        except InvalidTokenError as _e:
            await self.async_refresh_access_token()
            res = await func(self, *args, **kwargs)
            return res
        except LoginRequiredError as _e:
            await self.async_login(self.username, self.password)
            res = await func(self, *args, **kwargs)
            return res
        except NetworkError as _e:
            res = await func(self, *args, **kwargs)
            return res

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

    def __init__(self, hass=None, data: dict = {}) -> None:
        self._hass = hass
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
        if self._hass:
            self.session = async_create_clientsession(self._hass)
        else:
            self.session = ClientSession()
        self._request_timeout = ClientTimeout(total=10)

    async def setup(self) -> None:
        """Set up the Bull IoT API."""
        await self.async_login(self.username, self.password)
        await self.async_get_all_devices_list_mos()
        await self.async_setup_ble_chargers()
        self.init_mqtt()
        _LOGGER.info("BullApi started")

    def destroy(self) -> None:
        """Destroy the Bull IoT API."""
        self.stop_mqtt()
        for device in self.device_list.values():
            charger = device.ble_charger
            if charger:
                # Clear the reference synchronously so a subsequent reload can
                # register its replacement even while this coordinator's
                # unsubscribe callbacks are being awaited.
                device.ble_charger = None
                self._hass.async_create_task(charger.async_stop())
        # FIXME: old devices are not removed during reload
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
        self.selected_families = data.get("selected_families")

    def select_family(self, selected_families):
        """Select the families to load devices."""
        self.selected_families = selected_families

    async def async_login(self, username: str, password: str) -> None:
        """Login through the current MosHome v2 password endpoint."""
        form_params = {
            "username": self.encrypt_sensitive_field(username),
            # The React Native layer derives this before calling the Android
            # network wrapper; the wrapper itself sends it unchanged.
            "password": self.encrypt_sha256(
                self.encrypt_sha256(password) + self.encrypt_sha256("GONGNIU")
            ),
        }
        res = await self.async_make_request(
            "POST",
            "/mos/uic/v2/auth/form",
            "application/x-www-form-urlencoded; charset=utf-8",
            {"Login_parameter": "APP_PWD"},
            urlencode(form_params),
            form_params=form_params,
        )

        if not res["success"]:
            # MosHome v2 intentionally returns one code for either credential,
            # so do not imply which field was wrong.
            if res["code"] == 901006:
                raise Exception("invalid_auth")
            if res["code"] == 901001:
                raise Exception("wrong_user")
            if res["code"] == 901015:
                raise Exception("wrong_pwd")
            raise Exception("login_error")

        self.username = username
        self.password = password
        self.access_token = res["result"]["access_token"]
        self.refresh_token = res["result"]["refresh_token"]
        self.openid = str(res["result"]["openid"])

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

    @retry
    async def async_refresh_access_token(self) -> None:
        """Obtain a valid access token."""
        payload = json.dumps(
            {
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            separators=(",", ":"),
        )
        res = await self.async_make_request(
            "POST",
            "/mos/uic/v1/auth/token",
            "application/json; charset=utf-8",
            {},
            payload,
        )

        self.access_token = res["result"]["access_token"]
        self.refresh_token = res["result"]["refresh_token"]

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
        self.families = res["result"]

    @retry
    async def async_switch_family(self, family_id: int) -> None:
        """Switch the family associated to a user."""
        await self.async_make_request(
            "POST",
            f"/v1/families/{family_id}/switch",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            "{}",
        )

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
        return res["result"]

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
        except ClientError as err:
            _LOGGER.debug("Telemetry request failed: %s", err)

    def init_mqtt(self) -> None:
        """Initialize the MQTT client."""
        clientId = "IOS@2.9.1@" + self.openid

        def on_connect(client, userdata, flags, rc: int):
            _LOGGER.info("Connected with result code: %d", rc)
            if rc != mqtt.MQTT_ERR_SUCCESS:
                return

            subscribe_rc, _ = client.subscribe(
                "/sys/app/down/account/bind_reply", qos=0
            )
            if subscribe_rc != mqtt.MQTT_ERR_SUCCESS:
                _LOGGER.warning(
                    "MQTT bind-reply subscription failed: rc=%d", subscribe_rc
                )
            payload = {
                "id": "msg_id_bind_85",
                "params": {"token": self.access_token},
                "request": {"clientId": clientId, "userId": self.openid},
                "version": "1.0",
            }
            client.publish("/sys/app/up/account/bind", json.dumps(payload))

        def on_message(cb, client, userdata, msg):
            _LOGGER.debug("MQTT message: %s", msg.payload)
            db = json.loads(msg.payload)
            if msg.topic.endswith("/account/bind_reply"):
                _LOGGER.info(
                    "MQTT account bind reply: code=%s message=%s",
                    db.get("code"),
                    db.get("message"),
                )
            elif db.get("method") == "thing.properties":
                iot_id = db["params"]["iotId"]
                items = db["params"]["items"]
                for identifier, info in items.items():
                    for (
                        flattened_key,
                        flattened_value,
                    ) in self._flatten_identifier_values(
                        identifier, info["value"]
                    ).items():
                        cb(iot_id, flattened_key, flattened_value)
            elif db.get("method") == "thing.status":
                iot_id = db["params"]["iotId"]
                info = db["params"]["status"]
                status_time = info.get("time")
                device = self.device_list.get(iot_id)
                if (
                    device
                    and status_time is not None
                    and device._last_status_time is not None
                    and status_time < device._last_status_time
                ):
                    _LOGGER.debug(
                        "Ignore stale MQTT status: iot_id=%s status=%s time=%s last_time=%s",
                        iot_id,
                        info["value"],
                        status_time,
                        device._last_status_time,
                    )
                    return
                if device and status_time is not None:
                    device._last_status_time = status_time
                cb(iot_id, "status", info["value"])

        client = mqtt.Client(client_id=clientId)
        client.on_connect = on_connect
        client.reconnect_delay_set(min_delay=15, max_delay=120)
        client.username_pw_set(self.openid, self.access_token)
        client.on_message = partial(on_message, self.on_message)
        client.connect_async("emqx-prod.iotbull.com", port=1883)
        client.loop_start()
        self.client = client

    def stop_mqtt(self) -> None:
        """Stop the MQTT client."""
        if self.client:
            self.client.loop_stop()

    def on_message(self, iot_id: str, identifier: str, value) -> None:
        """Handle the MQTT message."""
        device = self.device_list.get(iot_id)
        if device:
            device.update_dp(identifier, value)

    @retry
    async def set_property(self, iot_id: str, identifier: str, value: int) -> None:
        """Set the device property."""
        await self.async_make_request(
            "PUT",
            f"/v1/dc/setDeviceProperty/{iot_id}",
            "application/json",
            {"Authorization": f"Bearer {self.access_token}"},
            json.dumps([{"value": value, "identifier": identifier}]),
        )

    @retry
    async def invoke_thing_service(self, iot_id: str, identifier: str) -> None:
        """Invoke thing service by identifier."""
        await self.async_make_request(
            "PUT",
            f"/mos/iot/v1/devices/{iot_id}/invokeThingService",
            "application/json",
            {
                "Authorization": f"Bearer {self.access_token}",
            },
            json.dumps({"identifier": identifier}),
        )

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

                _LOGGER.debug("Request: %s %s %s", path, response.status, text)

                try:
                    res = json.loads(text)
                except json.JSONDecodeError as err:
                    _LOGGER.error("Invalid JSON response for %s: %s", path, text)
                    raise NetworkError("invalid_response") from err
        except ClientError as err:
            _LOGGER.error("Request failed: %s %s", path, err)
            raise NetworkError("connection_failed") from err

        if not res.get("success"):
            if res.get("error") == "invalid_token":
                raise InvalidTokenError
            # {"code":9008,"message":"请重新登录","result":null,"success":false}
            if res.get("code") == 9008:
                raise LoginRequiredError

        return res
