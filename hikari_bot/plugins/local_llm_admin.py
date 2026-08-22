"""Administrator commands for the local LM Studio chat feature."""

from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.feature_flags import (
    get_local_llm_public_enabled,
    set_local_llm_public_enabled,
)


local_llm_access_toggle = on_cmd(
    "切换AI权限",
    aliases={"切换本地模型权限"},
    permission=SUPERUSER,
)


@local_llm_access_toggle.handle()
async def _():
    enabled = not await get_local_llm_public_enabled()
    await set_local_llm_public_enabled(enabled)
    if enabled:
        await local_llm_access_toggle.finish("已切换为所有群成员 @我均可使用本地模型。")
    await local_llm_access_toggle.finish("已切换为仅管理员 @我可使用本地模型。")
