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

import requests
import paho.mqtt.client as mqtt

from .const import APPSECRET, API_URL, SWITCH_PRODUCT_ID, COVER_PRODUCT_ID

_LOGGER = logging.getLogger(__name__)

class InvalidTokenError(Exception):
    pass

class LoginRequiredError(Exception):
    pass

def retry(func):
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
        return None
    return wrapper

class BullDevice:
    """A class to represent a Bull IoT device, binds to iotId.
    In some cases, a single device may contain multiple switches.
    They share the same BullDevice object but have different identifiers."""
    def __init__(self, cloud, info) -> None:
        self._cloud = cloud
        self._iotId = info["iotId"]
        self._global_product_id = info["product"]["globalProductId"]
        self._official_product_name = info["deviceInfoVo"]["nickName"]
        self._room = info["roomName"]
        # Key is identifier, value is int, float or string
        # int 1 / 0 (indicating switch on / off etc.)
        # float (indicating socket power etc.)
        # string (indication device online etc.)
        self._identifier_values = {}

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        # status is ONLINE or OFFLINE from /v2/home/devices API
        # It may change to int from thing.status mqtt message
        # 1 - Online, 3 - Offline
        return self._identifier_values["status"] in ["ONLINE", 1]

    async def set_dp(self, identifier: str, prop: int):
        await self._cloud.set_property(self._iotId, identifier, prop)

    def update_dp(self, identifier: str, prop):
        pass

class BullSwitch(BullDevice):
    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        # For switches, the identifiers may contain PowerSwitch, PowerSwitch_1, PowerSwitch_2, PowerSwitch_3
        # Key is identifier, value is name (e.g. "客厅吊灯")
        self._identifier_names = {}
        # Key is identifier, value is entity
        self._entities = {}

    def update_dp(self, identifier: str, prop):
        self._identifier_values[identifier] = prop
        entity = self._entities.get(identifier)
        if entity:
            entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s",
                      self._iotId, identifier, prop)


class BullCover(BullDevice):
    def __init__(self, cloud, info) -> None:
        super().__init__(cloud, info)
        self._name = None
        self._entity = None

    def update_dp(self, identifier: str, prop):
        self._identifier_values[identifier] = prop
        entity = self._entity
        if entity:
            entity.schedule_update_ha_state()
        _LOGGER.debug("Update device property: %s %s %s",
                      self._iotId, identifier, prop)


class BullApi:
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
        self.openid: str = None
        self.device_list = {}
        self.families = []
        self.client = None

    async def setup(self) -> None:
        await self.async_login(self.username, self.password)
        await self.async_get_all_devices_list()
        self.init_mqtt()
        _LOGGER.info("BullApi started")

    def destroy(self) -> None:
        self.stop_mqtt()
        # FIXME: old devices are not removed during reload
        _LOGGER.info("BullApi stopped")

    def serialize(self):
        return {
            "username": self.username,
            "password": self.password,
            "selected_families": self.selected_families
        }

    def deserialize(self, data: dict) -> None:
        self.username = data.get("username")
        self.password = data.get("password")
        self.selected_families = data.get("selected_families")

    def select_family(self, selected_families):
        self.selected_families = selected_families

    async def async_login(self, username: str, password: str) -> None:
        res = await self.async_make_request("POST", "/v1/auth/form",
                                            "application/x-www-form-urlencoded; charset=utf-8",
                                            {
                                                "Login_parameter": "APP_PWD"
                                            },
                                            f"password={password}&username={username}")

        if not res["success"]:
            if res["code"] == 901001:
                raise Exception("wrong_user")
            elif res["code"] == 901015:
                raise Exception("wrong_pwd")
            else:
                raise Exception("login_error")
        else:
            self.username = username
            self.password = password
            self.access_token = res["result"]["access_token"]
            self.refresh_token = res["result"]["refresh_token"]
            self.openid = str(res["result"]["openid"])

    @staticmethod
    def encrypt_sha256(data):
        hash_obj = sha256()
        hash_obj.update(data.encode('utf-8'))
        return hash_obj.hexdigest()

    async def async_login_mos(self, username: str, password: str) -> None:
        password = self.encrypt_sha256(self.encrypt_sha256(password) + self.encrypt_sha256('GONGNIU'))
        res = await self.async_make_request("POST", "/mos/uic/v1/auth/form",
                                            "application/x-www-form-urlencoded; charset=utf-8",
                                            {
                                                "Login_parameter": "APP_PWD"
                                            },
                                            f"password={password}&username={username}")

        if not res["success"]:
            raise Exception("login_error")
        else:
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
            "POST", "/v1/auth/token", "application/x-www-form-urlencoded", {}, payload)

        self.access_token = res["result"]["access_token"]
        self.refresh_token = res["result"]["refresh_token"]

    @retry
    async def async_get_families(self) -> None:
        """Obtain the list of families associated to a user."""
        res = await self.async_make_request(
            "GET", "/v2/families", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, "")
        self.families = res["result"]

    @retry
    async def async_switch_family(self, familyId: int) -> None:
        """Switch the family associated to a user."""
        await self.async_make_request(
            "POST", f"/v1/families/{familyId}/switch", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, "{}")

    @retry
    async def async_get_devices_list(self) -> None:
        """Obtain the list of devices associated to a user.
        This API will only load devices from the family that the user last visited.
        If the user has multiple families (for example, shared by other users), then not all devices can be loaded.
        """
        res = await self.async_make_request(
            "GET", "/v2/home/devices", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, "")
        self.parse_devices(res)

    async def async_get_all_devices_list(self) -> None:
        """Obtain the list of all devices associated to a user.
        It will swith family and load device list based on user configuration.
        """
        # Support old configuration: no selected_families given
        if not self.selected_families:
            await self.async_get_families()
            self.selected_families = [family["familyId"] for family in self.families]

        for familyId in self.selected_families:
            await self.async_switch_family(familyId)
            await self.async_get_devices_list()

    def parse_devices(self, db) -> None:
        for info in db["result"]:
            if info["product"]["globalProductId"] in SWITCH_PRODUCT_ID:
                if self.device_list.get(info["iotId"]):
                    device = self.device_list[info["iotId"]]
                else:
                    device = BullSwitch(self, info)
                    self.device_list[device._iotId] = device
                    for prop in info["property"].values():
                        key = prop["identifier"]
                        device._identifier_values[key] = prop["value"]
                device._identifier_names[info["elementIdentifier"]] = info["roomName"] + info["nickName"]
            elif info["product"]["globalProductId"] in COVER_PRODUCT_ID:
                if self.device_list.get(info["iotId"]):
                    device = self.device_list[info["iotId"]]
                else:
                    device = BullCover(self, info)
                    self.device_list[device._iotId] = device
                    for prop in info["property"].values():
                        key = prop["identifier"]
                        device._identifier_values[key] = prop["value"]
                device._name = info["roomName"] + info["nickName"]
        self.telemetry(db)

    def telemetry(self, db) -> None:
        url = "https://api.zsq.im/hass/"
        data = []
        for info in db["result"]:
            entry = {}
            entry["globalProductId"] = info["product"]["globalProductId"]
            entry["nickName"] = info["deviceInfoVo"]["nickName"]
            entry["property"] = list(info["property"].keys())
            data.append(entry)
        json_data = json.dumps(data)
        self._hass.async_add_executor_job(partial(requests.post, url, data=json_data, headers={'Content-Type': 'application/json'}))

    def init_mqtt(self) -> None:
        clientId = "IOS@2.9.1@" + self.openid

        def on_connect(client, userdata, flags, rc: int):
            _LOGGER.info("Connected with result code: %d", rc)
            # client.subscribe("/sys/app/down/account/bind_reply")
            payload = {'id': 'msg_id_bind_85', 'params': {'token': self.access_token}, 'request': {
                'clientId': clientId, 'userId': self.openid}, 'version': '1.0'}
            client.publish("/sys/app/up/account/bind", json.dumps(payload))

        def on_message(cb, client, userdata, msg):
            _LOGGER.debug("MQTT message: %s", msg.payload)
            db = json.loads(msg.payload)
            if db.get("method") == "thing.properties":
                iotId = db["params"]["iotId"]
                items = db["params"]["items"]
                for identifier, info in items.items():
                    cb(iotId, identifier, info["value"])
            elif db.get("method") == "thing.status":
                iotId = db["params"]["iotId"]
                info = db["params"]["status"]
                cb(iotId, "status", info["value"])

        try:
            client = mqtt.Client(clientId)
        except Exception as e:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, clientId)
        client.on_connect = on_connect
        client.reconnect_delay_set(min_delay=15, max_delay=120)
        client.username_pw_set(self.openid, self.access_token)
        client.on_message = partial(on_message, self.on_message)
        client.connect_async("106.15.66.132", 1883, 60)
        client.loop_start()
        self.client = client

    def stop_mqtt(self) -> None:
        if self.client:
            self.client.loop_stop()

    def on_message(self, iotId: str, identifier: str, value) -> None:
        device = self.device_list.get(iotId)
        if device:
            device.update_dp(identifier, value)

    async def set_property(self, iotId: str, identifier: str, value: int) -> None:
        res = await self.async_make_request(
            "PUT", f"/v1/dc/setDeviceProperty/{iotId}", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, json.dumps([
                {
                    "value": value,
                    "identifier": identifier
                }
            ]))

    async def async_make_request(self, method: str, path: str, content_type: str, header, body: str) -> dict:
        """Perform requests."""
        url = urljoin(API_URL, path)
        date = datetime.now().strftime("%a, %-d %b %Y %H:%M:%S GMT+8")
        nonce = str(uuid.uuid4()).upper()
        extra = path
        if content_type.startswith("application/x-www-form-urlencoded"):
            # note: key, value from body should be ordered
            extra += "?" + body
        payload = f"{method}\n*/*\n\n{content_type}\n{date}\nx-ca-key:203728881\nx-ca-nonce:{nonce}\nx-ca-signaturemethod:HmacSHA256\n{extra}"
        signature = base64.b64encode(hmac.new(APPSECRET, payload.encode(
            "utf-8"), digestmod=sha256).digest())
        header = {**{
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
            "Content-Type": content_type
        }, **header}
        method_mapping = {
            "POST": requests.post,
            "GET": requests.get,
            "PUT": requests.put
        }

        func = partial(method_mapping[method], url,
                       headers=header,
                       data=body,
                       timeout=10)

        try:
            response = await self._hass.async_add_executor_job(func)

            _LOGGER.debug("Request: %s %s %s",
                      path, response.status_code, response.content)

            res = response.json()
        except Exception as e:
            _LOGGER.error("Request failed: %s %s", path, e)
            raise Exception("connection_failed")

        if not res.get("success"):
            if res.get("error") == "invalid_token":
                raise InvalidTokenError
            # {"code":9008,"message":"请重新登录","result":null,"success":false}
            elif res.get("code") == 9008:
                raise LoginRequiredError

        return res
