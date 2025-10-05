import asyncio
import aiohttp
import json
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot
from hikari_bot.utils.constants import *
from hikari_bot.utils.mycard import *
from hikari_bot.plugins.common import *

_stop_event = asyncio.Event()

_ws_task: asyncio.Task | None = None

room_list = {}
watching_list = []

async def _room_add_players(room_id, player_ids):
    for i in range(2):
        player_info = await fetch_player_info(player_ids[i])
        room_list.setdefault(room_id, []).append(player_info)


async def _send_notifications(bot: Bot, subscribers: list, message: str):
    for subscriber in subscribers:
        try:
            usertype, qq = subscriber
            if usertype == "group":
                await bot.send_group_msg(group_id=int(qq), message=message)
            else:
                await bot.send_private_msg(user_id=int(qq), message=message)
        except Exception as e:
            await message_superusers(bot, f"发送通知失败: {e}")


async def handle_create_event(bot: Bot, player_ids: list):
    try:
        subscribe_list = get_subscribe_list()
        for i in range(2):
            player_id = player_ids[i]
            if player_id in subscribe_list and not await is_first_win(player_id):
                watching_list.append(player_id)
                message = f"您关注的{player_id}已开始挑战首赢，对手id：{player_ids[1-i]}。"
                asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message))
    except Exception as e:
        asyncio.create_task(message_superusers(bot, f"处理create事件出错: {e}"))


async def handle_delete_event(bot: Bot, room_id):
    try:
        subscribe_list = get_subscribe_list()
        if room_id not in room_list:
            asyncio.create_task(message_superusers(bot, f"房间不在列表中：{room_id}"))
            return
        
        player_infos = room_list[room_id]
        del room_list[room_id]

        player_ids = [info["username"] for info in player_infos if info.get("username")]
        player_infos_now = [await fetch_player_info(player_id) for player_id in player_ids]
        pt_deltas = [info["pt"] - player_infos[i]["pt"] for i, info in enumerate(player_infos_now) if info and info.get("pt") is not None]
        pt_strs = [f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}" for delta in pt_deltas]

        asyncio.create_task(message_superusers(bot, f"对局已完成：{player_ids[0]}({pt_strs[0]}) vs {player_ids[1]}({pt_strs[1]})"))

        for i, player_id in enumerate(player_ids):
            if player_id in watching_list:
                watching_list.remove(player_id)
                if pt_deltas[i] > 0:
                    message = f"您关注的{player_id}成功拿下首赢！pt变动：{pt_strs[i]}。"
                    asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message))
    except Exception as e:
        asyncio.create_task(message_superusers(bot, f"处理delete事件出错: {e}"))


async def process_mycard_event(bot: Bot, payload: dict):
    event = payload.get("event") or "?"
    data  = payload.get("data") or {}

    if event == "init":
        for match in data:
            users = match.get("users") or []
            player_ids = [user.get("username") for user in users if user.get("username")]
            room_id = match.get("id")
            if len(player_ids) == 2:
                asyncio.create_task(_room_add_players(room_id, player_ids))

    elif event == "create":
        users = data.get("users") or []
        player_ids = [user.get("username") for user in users if user.get("username")]
        room_id = data.get("id")
        if len(player_ids) == 2:
            await _room_add_players(room_id, player_ids)
            await _handle_create_event(bot, player_ids)

    elif event == "delete":
        room_id = data
        await _handle_delete_event(bot, room_id)


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
                                logger.info(f"[mycard raw] {msg.data}")
                                await process_mycard_event(bot, data)
                            except Exception:
                                logger.exception(f"[mycard raw] {msg.data}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("[mycard] 连接被关闭或出错，准备重连")
                            break
        except Exception:
            logger.exception(f"[mycard] WS 出错")
        
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
    
    _ws_task = asyncio.create_task(ws_runner(bot))
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
