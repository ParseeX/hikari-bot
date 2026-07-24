from hikari_bot.core.config import settings

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from hikari_bot.core.logger import new_log_file
from hikari_bot.persistence import initialize_default_state_store

new_log_file()
initialize_default_state_store()

nonebot.init(
    superusers=set(settings.superusers),
    command_start={""},
)

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

nonebot.load_from_toml("pyproject.toml")

if __name__ == "__main__":
    nonebot.run()
