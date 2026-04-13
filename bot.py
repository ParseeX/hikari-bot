import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from hikari_bot.core.logger import new_log_file

import sys
import os

sys.path.append(os.path.dirname(__file__))

new_log_file()

nonebot.init(superusers={"909333601"},command_start={"","/"})

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()