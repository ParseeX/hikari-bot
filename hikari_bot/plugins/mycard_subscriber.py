import asyncio
import aiohttp
import json
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot
from hikari_bot.utils.constants import *
from hikari_bot.utils.mycard import get_subscribe_list

_stop_event = asyncio.Event()

_ws_task: asyncio.Task | None = None


async def process_mycard_event(bot: Bot, payload: dict):
    event = payload.get("event") or "?"
    data  = payload.get("data") or {}

    if event == "create":
        users = data.get("users") or []
        ids = [user.get("username") for user in users if user.get("username")]
        if len(ids) != 2:
            logger.warning(f"[mycard] 无法处理的对局数据：{data}")
            return
        
        for i in range(2):
            id = ids[i]
            subscribe_list = get_subscribe_list()
            if id in subscribe_list:
                message = f"您关注的{id}已开始对局，对手id：{ids[1-i]}。"
                for subscriber in subscribe_list[id]:
                    [usertype, qq] = subscriber
                    if usertype == "group":
                        await bot.send_group_msg(group_id=int(qq), message=message)
                    else:
                        await bot.send_private_msg(user_id=int(qq), message=message)


async def ws_runner(bot: Bot):
    backoff = 1
    max_backoff = 60
    import random
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                    logger.info("[mycard] 已连接到 WS")
                    backoff = 1  # 连接成功后重置退避
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await process_mycard_event(bot, data)
                            except Exception:
                                logger.warning(f"[mycard raw] {msg.data}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("[mycard] 连接被关闭或出错，准备重连")
                            break
        except Exception as e:
            logger.error(f"[mycard] WS 出错：{e}")
        
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


driver = get_driver()
@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    global _ws_task
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except Exception:
            pass
        logger.info("[mycard] 已取消旧的 WS 任务")
    
    _ws_task = asyncio.create_task(ws_runner())
    asyncio.create_task(ws_runner(bot))
    logger.info("[mycard] WS 监听任务已启动")


@driver.on_shutdown
async def _on_shutdown():
    global _ws_task
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except Exception:
            pass
        logger.info("[mycard] WS 任务已停止")
