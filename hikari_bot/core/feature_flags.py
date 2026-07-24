import asyncio

from hikari_bot.persistence import get_state_store


async def get_notify_enabled() -> bool:
    """读取 MyCard 通知开关；未配置时默认开启。"""
    return await asyncio.to_thread(
        get_state_store().get_flag, "mycard_notify", True
    )

async def set_notify_enabled(value: bool) -> None:
    """设置 MyCard 通知开关，不阻塞事件循环。"""
    await asyncio.to_thread(get_state_store().set_flag, "mycard_notify", bool(value))

async def get_mensa_enabled() -> bool:
    """读取食堂监控开关；未配置时默认开启。"""
    return await asyncio.to_thread(
        get_state_store().get_flag, "mensa_monitor", True
    )

async def set_mensa_enabled(value: bool) -> None:
    """设置食堂监控开关，不阻塞事件循环。"""
    await asyncio.to_thread(get_state_store().set_flag, "mensa_monitor", bool(value))
