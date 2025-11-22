"""API interactions for bull-iot integration."""

from datetime import datetime
import uuid
import hmac
import base64
from hashlib import sha256
from urllib.parse import urljoin
from functools import partial
import json
import logging

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import paho.mqtt.client as mqtt

from .const import (
    APPSECRET,
    API_URL,
    SWITCH_PRODUCT_ID,
    COVER_PRODUCT_ID,
    CHARGER_PRODUCT_ID,
)

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
        self.product_name = ""
        self.model_name = ""
        self.firmware_version = ""
        self.room = info["roomName"]
        # Key is identifier, value is int, float or string
        # int 1 / 0 (indicating switch on / off etc.)
        # float (indicating socket power etc.)
        # string (indication device online etc.)
        self.identifier_values = {}

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        # status is ONLINE or OFFLINE from /v2/home/devices API
        # It may change to int from thing.status mqtt message
        # 1 - Online, 3 - Offline
        return self.identifier_values["status"] in ["ONLINE", 1]

    async def set_dp(self, identifier: str, prop: int):
        await self._cloud.set_property(self.iot_id, identifier, prop)

    def update_dp(self, identifier: str, prop):
        pass


class BullSwitch(BullDevice):
    """A class to represent a Bull IoT switch device."""

    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        # For switches, the identifiers may contain PowerSwitch, PowerSwitch_1, PowerSwitch_2, PowerSwitch_3
        # Key is identifier, value is name (e.g. "客厅吊灯")
        self.identifier_names = {}
        # Key is identifier, value is entity
        self._entities = {}

    def update_dp(self, identifier: str, prop):
        self.identifier_values[identifier] = prop
        entity = self._entities.get(identifier)
        if entity:
            entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s", self.iot_id, identifier, prop)


class BullCover(BullDevice):
    """A class to represent a Bull IoT cover device."""

    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        self.name = None
        self._entity = None

    def update_dp(self, identifier: str, prop):
        self.identifier_values[identifier] = prop
        entity = self._entity
        if entity:
            entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s", self.iot_id, identifier, prop)


class BullApi:
    """A class to represent the Bull IoT API."""

    def __init__(self, hass, data: dict = {}) -> None:
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
        self.session = async_create_clientsession(hass)
        self._request_timeout = ClientTimeout(total=10)

    async def setup(self) -> None:
        """Set up the Bull IoT API."""
        await self.async_login(self.username, self.password)
        await self.async_get_all_devices_list()
        self.init_mqtt()
        _LOGGER.info("BullApi started")

    def destroy(self) -> None:
        """Destroy the Bull IoT API."""
        self.stop_mqtt()
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
        """Login to the Bull IoT API."""
        res = await self.async_make_request(
            "POST",
            "/v1/auth/form",
            "application/x-www-form-urlencoded; charset=utf-8",
            {"Login_parameter": "APP_PWD"},
            f"password={password}&username={username}",
        )

        if not res["success"]:
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

    async def async_login_mos(self, username: str, password: str) -> None:
        """Login to the Bull IoT API (MosHome)."""
        password = self.encrypt_sha256(
            self.encrypt_sha256(password) + self.encrypt_sha256("GONGNIU")
        )
        res = await self.async_make_request(
            "POST",
            "/mos/uic/v1/auth/form",
            "application/x-www-form-urlencoded; charset=utf-8",
            {"Login_parameter": "APP_PWD"},
            f"password={password}&username={username}",
        )

        if not res["success"]:
            raise Exception("login_error")

        self.username = username
        self.password = password
        self.access_token = res["result"]["access_token"]
        self.refresh_token = res["result"]["refresh_token"]
        self.openid = str(res["result"]["openid"])

    @retry
    async def async_refresh_access_token(self) -> None:
        """Obtain a valid access token."""
        payload = f"client_id=paascloudclientuic&client_secret=paascloudClientSecret&grant_type=refresh_token&refresh_token={self.refresh_token}"
        res = await self.async_make_request(
            "POST", "/v1/auth/token", "application/x-www-form-urlencoded", {}, payload
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

    async def async_parse_device(self, info: dict) -> None:
        """Parse the device information."""
        if info["product"]["globalProductId"] in SWITCH_PRODUCT_ID | CHARGER_PRODUCT_ID:
            if self.device_list.get(info["iotId"]):
                device = self.device_list[info["iotId"]]
            else:
                device = BullSwitch(self, info)
                await self.async_add_new_device(device, info)
            device.identifier_names[info["elementIdentifier"]] = (
                info["roomName"] + info["nickName"]
            )
        elif info["product"]["globalProductId"] in COVER_PRODUCT_ID:
            if self.device_list.get(info["iotId"]):
                device = self.device_list[info["iotId"]]
            else:
                device = BullCover(self, info)
                await self.async_add_new_device(device, info)
            device.name = info["roomName"] + info["nickName"]
        else:
            # Add unsupported devices anyway
            if self.device_list.get(info["iotId"]):
                device = self.device_list[info["iotId"]]
            else:
                device = BullDevice(self, info)
                await self.async_add_new_device(device, info)
            _LOGGER.warning(
                "Unsupported device: %s %s %s",
                device.iot_id,
                device.product_name,
                device.model_name,
            )

    async def async_add_new_device(self, device: BullDevice, info: dict) -> None:
        """Add a new device to the device list."""
        self.device_list[device.iot_id] = device
        for prop in info["property"].values():
            key = prop["identifier"]
            device.identifier_values[key] = prop["value"]
        device_info = await self.async_get_device_info(device.iot_id)
        device.product_name = device_info["productName"]
        device.model_name = device_info["modelName"]
        device.firmware_version = device_info["firmwareVersion"]

    async def async_parse_devices(self, db) -> None:
        """Parse the devices information."""
        for info in db["result"]:
            await self.async_parse_device(info)
        self._hass.async_create_task(self.telemetry())

    @retry
    async def async_get_rooms_mos(self) -> None:
        """Obtain the list of rooms associated to a user.
        This API will only load devices from the family that the user last visited.
        If the user has multiple families (for example, shared by other users), then not all devices can be loaded.
        """
        res = await self.async_make_request(
            "GET",
            "/mos/home/v2/rooms",
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
            # client.subscribe("/sys/app/down/account/bind_reply")
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
            if db.get("method") == "thing.properties":
                iot_id = db["params"]["iotId"]
                items = db["params"]["items"]
                for identifier, info in items.items():
                    cb(iot_id, identifier, info["value"])
            elif db.get("method") == "thing.status":
                iot_id = db["params"]["iotId"]
                info = db["params"]["status"]
                cb(iot_id, "status", info["value"])

        client = mqtt.Client(client_id=clientId)
        client.on_connect = on_connect
        client.reconnect_delay_set(min_delay=15, max_delay=120)
        client.username_pw_set(self.openid, self.access_token)
        client.on_message = partial(on_message, self.on_message)
        client.connect_async("106.15.66.132")
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

    async def async_make_request(
        self, method: str, path: str, content_type: str, header, body: str
    ) -> dict:
        """Perform requests."""
        url = urljoin(API_URL, path)
        date = datetime.now().strftime("%a, %-d %b %Y %H:%M:%S GMT+8")
        nonce = str(uuid.uuid4()).upper()
        extra = path
        if content_type.startswith("application/x-www-form-urlencoded"):
            # note: key, value from body should be ordered
            extra += "?" + body
        payload = f"{method}\n*/*\n\n{content_type}\n{date}\nx-ca-key:203728881\nx-ca-nonce:{nonce}\nx-ca-signaturemethod:HmacSHA256\n{extra}"
        signature = base64.b64encode(
            hmac.new(APPSECRET, payload.encode("utf-8"), digestmod=sha256).digest()
        ).decode()
        header = {
            **{
                "Host": "api.iotbull.com",
                "X-Ca-Key": "203728881",
                "X-App-Platform": "ios",
                "X-Ca-Signaturemethod": "HmacSHA256",
                "Content-Md5": "",
                "X-App-Version": "2.3.1",
                "X-Ca-Signature-Headers": "x-ca-key,x-ca-nonce,x-ca-signaturemethod",
                "Authorization": "Basic cGFhc2Nsb3VkY2xpZW50dWljOnBhYXNjbG91ZENsaWVudFNlY3JldA==",
                "Accept-Language": "zh-Hans;q=1, zh-Hant-CN;q=0.9, en-CN;q=0.8",
                "Accept": "*/*",
                "Accept-Encoding": "gzip",
                "Date": date,
                "X-Ca-Nonce": nonce,
                "X-Ca-Signature": signature,
                "Content-Type": content_type,
            },
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
