import asyncio
import logging
import dotenv
import os
from custom_components.bull.api import BullApi

logging.basicConfig(level=logging.DEBUG)
dotenv.load_dotenv()


async def test():
    username = os.getenv("BULL_USERNAME") or input("请输入您的用户名: ")
    password = os.getenv("BULL_PASSWORD") or input("请输入您的密码: ")
    bull_api = BullApi()
    await bull_api.async_login_mos(username, password)
    await bull_api.async_get_families()
    for family in bull_api.families:
        print(family)
        await bull_api.async_switch_family(family["familyId"])
        await bull_api.async_get_rooms_mos()
    print(bull_api.device_list)


if __name__ == "__main__":
    asyncio.run(test())
