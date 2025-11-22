import asyncio
import logging
import dotenv
import os
from custom_components.bull.api import BullApi
from homeassistant.util.hass_dict import HassDict
from homeassistant.components.network import Network
from homeassistant.core_config import Config
from homeassistant.core import EventBus

logging.basicConfig(level=logging.DEBUG)
dotenv.load_dotenv()


class FakeHass:
    def __init__(self):
        self.data = HassDict()
        self.config = Config(self, os.path.dirname(__file__))
        self.bus = EventBus(self)
        self.data["network"] = Network(self)

    async def async_create_task(self, cb):
        return cb()


async def test():
    username = os.getenv("BULL_USERNAME") or input("请输入您的用户名: ")
    password = os.getenv("BULL_PASSWORD") or input("请输入您的密码: ")
    bull_api = BullApi(FakeHass())
    await bull_api.async_login_mos(username, password)
    await bull_api.async_get_families()
    for family in bull_api.families:
        print(family)
        await bull_api.async_switch_family(family["familyId"])
        await bull_api.async_get_rooms_mos()
    print(bull_api.device_list)


if __name__ == "__main__":
    asyncio.run(test())
