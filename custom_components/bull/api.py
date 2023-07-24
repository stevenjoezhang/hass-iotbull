from .const import APPSECRET, API_URL
import requests
import paho.mqtt.client as mqtt
from datetime import datetime
import uuid
import hmac
import base64
from hashlib import sha256
from urllib.parse import urljoin
from functools import partial
import json
from typing import Callable

import logging
_LOGGER = logging.getLogger(__name__)


class BullDevice:
    def __init__(self, cloud, info) -> None:
        self._iotId = info["iotId"]
        self._identifier = info["elementIdentifier"]
        self._value: int = info["property"][self._identifier]["value"]
        self._cloud = cloud

        self.name = info["roomName"] + info["nickName"]
        self.unique_id = self._iotId + "." + self._identifier
        self.entity = None

    @property
    def is_on(self) -> bool:
        """Check if Bull IoT switch is on."""
        return self._value

    async def set_dp(self, prop: bool):
        await self._cloud.set_property(self._iotId, self._identifier, int(prop))

    def update_dp(self, prop: int):
        self._value = prop
        if self.entity:
            self.entity.async_write_ha_state()
        _LOGGER.debug("Update device property: %s %s %d",
                      self._iotId, self._identifier, self._value)


class BullApi:
    def __init__(self, hass, data: dict = {}) -> None:
        self._hass = hass
        if data:
            self.deserialize(data)
        else:
            self.username = None
            self.password = None
            self.access_token = None
            self.refresh_token = None
            self.openid: str = None
        self.device_list = {}

    def destroy(self) -> None:
        self.stop_mqtt()

    def serialize(self):
        return {
            "username": self.username,
            "password": self.password,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "openid": self.openid
        }

    def deserialize(self, data: dict) -> None:
        self.username = data["username"]
        self.password = data["password"]
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.openid = data["openid"]

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

    async def async_refresh_access_token(self) -> None:
        """Obtain a valid access token."""
        payload = f"client_id=paascloudclientuic&client_secret=paascloudClientSecret&grant_type=refresh_token&refresh_token={self.refresh_token}"
        res = await self.async_make_request(
            "POST", "/v1/auth/token", "application/x-www-form-urlencoded", {}, payload)

        if not res["success"]:
            # {"code":9008,"message":"请重新登录","result":null,"success":false}
            if res["code"] == 9008:
                await self.async_login(self.username, self.password)
            else:
                raise Exception("refresh_token_error")
        else:
            self.access_token = res["result"]["access_token"]
            self.refresh_token = res["result"]["refresh_token"]

    async def async_get_devices_list(self, double_fault=False) -> None:
        """Obtain the list of devices associated to a user."""
        res = await self.async_make_request(
            "GET", "/v2/home/devices", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, "")

        if not res.get("success"):
            if res.get("error") == "invalid_token":
                if not double_fault:
                    await self.async_refresh_access_token()
                    await self.async_get_devices_list(True)
                else:
                    raise Exception("invalid_token")
            elif res.get("code") == 9008:
                if not double_fault:
                    await self.async_login(self.username, self.password)
                    await self.async_get_devices_list(True)
                else:
                    raise Exception("invalid_token")
            else:
                raise Exception("get_devices_error")
        else:
            self.parse_devices(res)

    def parse_devices(self, db) -> None:
        for info in db["result"]:
            if info["product"]["globalProductId"] in [4, 5, 6, 13]:
                device = BullDevice(self, info)
                self.device_list[device.unique_id] = device

    async def init_mqtt(self) -> None:
        clientId = "IOS@2.9.1@" + self.openid

        def on_connect(client, userdata, flags, rc):
            _LOGGER.info(f"Connected with result code {rc}")
            # client.subscribe("/sys/app/down/account/bind_reply")
            payload = {'id': 'msg_id_bind_85', 'params': {'token': self.access_token}, 'request': {
                'clientId': clientId, 'userId': self.openid}, 'version': '1.0'}
            client.publish("/sys/app/up/account/bind", json.dumps(payload))

        def on_message(cb: Callable[[str, str, int]], client, userdata, msg):
            db = json.loads(msg.payload)
            if db.get("method") == "thing.properties":
                iotId = db["params"]["iotId"]
                items = db["params"]["items"]
                for identifier, info in items.items():
                    cb(iotId, identifier, info["value"])

        client = mqtt.Client(clientId)
        client.on_connect = on_connect
        client.username_pw_set(self.openid, self.access_token)
        client.on_message = partial(on_message, self.on_message)
        client.connect_async("106.15.66.132", 1883, 60)
        client.loop_start()
        self.client = client

    def stop_mqtt(self) -> None:
        if self.client:
            self.client.loop_stop()

    def on_message(self, iotId: str, identifier: str, value: int) -> None:
        unique_id = iotId + "." + identifier
        device = self.device_list.get(unique_id)
        if device:
            device.update_dp(value)

    async def set_property(self, iotId: str, identifier: str, value: int, double_fault=False) -> None:
        res = await self.async_make_request(
            "PUT", f"/v1/dc/setDeviceProperty/{iotId}", "application/json", {
                "Authorization": f"Bearer {self.access_token}"
            }, json.dumps([
                {
                    "value": value,
                    "identifier": identifier
                }
            ]))
        if not res.get("success"):
            if res.get("error") == "invalid_token":
                if not double_fault:
                    await self.async_refresh_access_token()
                    await self.set_property(iotId, identifier, value, True)
                else:
                    raise Exception("invalid_token")
            elif res.get("code") == 9008:
                if not double_fault:
                    await self.async_login(self.username, self.password)
                    await self.async_get_devices_list(True)
                else:
                    raise Exception("invalid_token")
            else:
                raise Exception("set_property_error")

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

            res = json.loads(response.content)
        except Exception:
            raise Exception("connection_failed")

        _LOGGER.debug("Request: %s %s",
                      path, response.content)
        return res
