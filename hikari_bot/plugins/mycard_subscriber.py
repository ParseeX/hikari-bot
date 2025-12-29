import asyncio
import aiohttp
import json
from nonebot import get_driver, logger, on_command
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11 import Bot
from hikari_bot.utils.constants import *
from hikari_bot.utils.mycard import *
from hikari_bot.utils.feature_flags import get_notify_enabled, set_notify_enabled
from hikari_bot.plugins.common import message_superusers

_stop_event = asyncio.Event()

_ws_task: asyncio.Task | None = None

room_list = {}

notify_switch = on_command("切换通知", permission=SUPERUSER)

async def _send_notifications(bot: Bot, subscribers: list, message: str, message_type: str):
    for subscriber in subscribers:
        try:
            usertype, qq = subscriber
            if message_type != "both" and usertype != message_type:
                continue
            if usertype == "group":
                await bot.send_group_msg(group_id=int(qq), message=message)
            else:
                await bot.send_private_msg(user_id=int(qq), message=message)
        except Exception as e:
            pass
            #await message_superusers(bot, f"发送通知失败: {e}")


async def handle_create_event(bot: Bot, player_ids: list):
    try:
        subscribe_list = get_subscribe_list()
        for i, player_id in enumerate(player_ids):
            if player_id in subscribe_list:
                message = f"您关注的{player_id}已开始对局，对手id：{player_ids[1-i]}。"
                asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message, "private"))
        if player_id[0] in subscribe_list and player_id[1] in subscribe_list:
            subscribers_0 = set(subscribe_list.get(player_ids[0], []))
            subscribers_1 = set(subscribe_list.get(player_ids[1], []))
            common_subscribers = subscribers_0 & subscribers_1
            message = f"您关注的{player_ids[0]}和{player_ids[1]}已开始对局。"
            asyncio.create_task(_send_notifications(bot, list(common_subscribers), message, "group"))
        
    except Exception as e:
        await message_superusers(bot, f"处理create事件出错: {e}")


async def handle_delete_event(bot: Bot, room_id):
    try:
        subscribe_list = get_subscribe_list()
        if room_id not in room_list:
            await message_superusers(bot, f"房间不在列表中：{room_id}")
            return
        
        player_ids = room_list[room_id]
        del room_list[room_id]

        if len(player_ids) != 2:
            await message_superusers(bot, f"房间玩家数异常：{room_id}，{player_ids}")
            return
        
        rec = None
        start_time = asyncio.get_event_loop().time()
        timeout = 180  # 3分钟 = 180秒
        retry_interval = 5  # 5秒
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            rec = await fetch_latest_record(player_ids[0])
            if rec and rec.get("usernameb") == player_ids[1]:
                break
            
            # 某些情况下，两个玩家的顺序会被交换
            player_ids[0], player_ids[1] = player_ids[1], player_ids[0]
            rec = await fetch_latest_record(player_ids[0])
            if rec and rec.get("usernameb") == player_ids[1]:
                break
            player_ids[0], player_ids[1] = player_ids[1], player_ids[0]

            # 都没有找到匹配的记录，等待5秒后重试
            rec = None
            logger.info(f"[mycard] 未找到匹配记录，5秒后重试... ({player_ids[0]} vs {player_ids[1]})")
            await asyncio.sleep(retry_interval)
        
        if rec is None:
            await message_superusers(bot, f"获取最新记录失败，已重试3分钟：{player_ids[0]} vs {player_ids[1]}")
            return

        pt_deltas = [rec["pta"] - rec["pta_ex"], rec["ptb"] - rec["ptb_ex"]]
        pt_strs = [f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}" for delta in pt_deltas]

        if get_notify_enabled():
            await message_superusers(bot, f"对局已完成：{player_ids[0]}({pt_strs[0]}) vs {player_ids[1]}({pt_strs[1]})")

        if rec["isfirstwin"]:
            for i, player_id in enumerate(player_ids):
                if player_id in subscribe_list:
                    if pt_deltas[i] > 0:
                        message = f"您关注的{player_id}成功拿下首赢！pt变动：{pt_strs[i]}。"
                        asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message, "both"))
        
    except Exception as e:
        await message_superusers(bot, f"处理delete事件出错: {e}")


async def process_mycard_event(bot: Bot, payload: dict):
    event = payload.get("event") or "?"
    data  = payload.get("data") or {}

    if event == "init":
        for match in data:
            users = match.get("users") or []
            player_ids = [user.get("username") for user in users if user.get("username")]
            room_id = match.get("id")
            if len(player_ids) == 2:
                room_list[room_id] = player_ids

    elif event == "create":
        users = data.get("users") or []
        player_ids = [user.get("username") for user in users if user.get("username")]
        room_id = data.get("id")
        if len(player_ids) == 2:
            room_list[room_id] = player_ids
            await handle_create_event(bot, player_ids)

    elif event == "delete":
        room_id = data
        asyncio.create_task(handle_delete_event(bot, room_id))


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

@notify_switch.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if get_notify_enabled():
        set_notify_enabled(False)
        await notify_switch.finish("已关闭MyCard对局通知。")
    else:
        set_notify_enabled(True)
        await notify_switch.finish("已开启MyCard对局通知。")