import asyncio
import aiohttp
import json
from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot
from hikari_bot.utils.constants import *
from hikari_bot.utils.mycard import *

_stop_event = asyncio.Event()

_ws_task: asyncio.Task | None = None

watching_list = {}

async def process_mycard_event(bot: Bot, payload: dict):
    event = payload.get("event") or "?"
    data  = payload.get("data") or {}

    if event == "init":
        for match in data:
            users = match.get("users") or []
            player_ids = [user.get("username") for user in users if user.get("username")]
            if len(player_ids) != 2:
                logger.warning(f"[mycard] 无法处理的对局数据：{match}")
                continue

            subscribe_list = get_subscribe_list()
            for i in range(2):
                player_id = player_ids[i]
                if player_id in subscribe_list and not is_first_win(player_id):
                    room_id = match.get("id")
                    watching_list.setdefault(room_id, []).append(player_id)
                    logger.info(f"[mycard] 关注的玩家{player_id}已开始对局。")

    elif event == "create":
        users = data.get("users") or []
        player_ids = [user.get("username") for user in users if user.get("username")]
        if len(player_ids) != 2:
            logger.warning(f"[mycard] 无法处理的对局数据：{data}")
            return
        
        subscribe_list = get_subscribe_list()
        for i in range(2):
            player_id = player_ids[i]
            if player_id in subscribe_list and not is_first_win(player_id):
                message = f"您关注的{player_id}已开始挑战首胜，对手id：{player_ids[1-i]}。"
                room_id = data.get("id")
                watching_list.setdefault(room_id, []).append(player_id)
                logger.info(f"[mycard] 关注的玩家{player_id}已开始对局。")
                for subscriber in subscribe_list.get(player_id, []):
                    usertype, qq = subscriber
                    if usertype == "group":
                        await bot.send_group_msg(group_id=int(qq), message=message)
                    else:
                        await bot.send_private_msg(user_id=int(qq), message=message)
    
    elif event == "delete":
        room_id = data
        if room_id in watching_list:
            logger.info(f"[mycard] 关注的对局已结束：{room_id}")
            player_ids = watching_list[room_id]
            del watching_list[room_id]

            subscribe_list = get_subscribe_list()
            for player_id in player_ids:
                rec = await fetch_latest_record_with_retry(player_id)
                pt_delta = rec["pta"]-rec["pta_ex"] if rec["usernamea"] == player_id else rec["ptb"]-rec["ptb_ex"]

                pt_str = f"+{pt_delta:.1f}" if pt_delta > 0 else f"{pt_delta:.1f}"
                if rec["winner"] == player_id:
                    message = f"您关注的{player_id}成功拿下首胜！pt变动：{pt_str}。"
                else:
                    message = f"您关注的{player_id}挑战首胜失败。pt变动：{pt_str}。"
                for subscriber in subscribe_list.get(player_id, []):
                    usertype, qq = subscriber
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
