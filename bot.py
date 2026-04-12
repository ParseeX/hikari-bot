import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from hikari_bot.utils.constants import *
import sys
import os
import datetime

sys.path.append(os.path.dirname(__file__))

nonebot.init(superusers={"909333601"},command_start={"","/"})

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)
driver.state.log_file = os.path.join(DATA_DIR, f"bot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()