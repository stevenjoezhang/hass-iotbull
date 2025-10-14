import asyncio
import logging
from http.client import HTTPConnection

putrequest = HTTPConnection.putrequest
# https://github.com/home-assistant/core/blob/2023.7.2/homeassistant/block_async_io.py
from custom_components.bull.api import BullApi

HTTPConnection.putrequest = putrequest

logging.basicConfig(level=logging.DEBUG)


class FakeHass:
    async def async_add_executor_job(self, cb):
        return cb()


async def test():
    username = input("请输入您的用户名: ")
    password = input("请输入您的密码: ")
    bull_api = BullApi(FakeHass())
    await bull_api.async_login_mos(username, password)
    await bull_api.async_get_families()
    for family in bull_api.families:
        print(family)
        await bull_api.async_switch_family(family["familyId"])
        await bull_api.async_get_devices_list()
    print(bull_api.device_list)


if __name__ == "__main__":
    asyncio.run(test())
