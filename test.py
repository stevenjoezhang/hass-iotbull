from http.client import HTTPConnection
putrequest = HTTPConnection.putrequest
# https://github.com/home-assistant/core/blob/2023.7.2/homeassistant/block_async_io.py
from custom_components.bull.api import BullApi
HTTPConnection.putrequest = putrequest

import os
import asyncio
import logging

logging.basicConfig(level=logging.DEBUG)

class FakeHass:
    async def async_add_executor_job(self, cb):
        return cb()

async def test():
    username = input('请输入您的用户名: ')
    password = input('请输入您的密码: ')
    bull_api = BullApi(FakeHass())
    await bull_api.async_login(username, password)
    await bull_api.async_get_devices_list()

if __name__ == '__main__':
    asyncio.run(test())
