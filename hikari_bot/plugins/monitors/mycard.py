"""
mycard.py — MyCard 对局实时监控插件

功能：
  - 通过 WebSocket 监听对局创建/结束事件
  - 对局结束后自动查询战绩并推送订阅通知
  - 连接断开时指数退避重连
  - 支持运行时开关对局结果通知
"""

import asyncio
import json
import re
import base64

import aiohttp

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import ActionFailed, Bot, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.config import settings
from hikari_bot.core.feature_flags import get_notify_enabled, set_notify_enabled
from hikari_bot.core.logger import log_message
from hikari_bot.core.whitelist import message_superusers
from hikari_bot.services.mycard import fetch_latest_record, get_subscribe_list, unsubscribe_all
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime
from hikari_bot.services.mycard_archive.presentation import read_rules
from hikari_bot.services.mycard_archive.ranking import RankFilter


# ── 常量与状态 ───────────────────────────────────────────────────────────────────────

WS_URL = "wss://tiramisu.moecube.com:8923/?filter=started"

_ws_task: asyncio.Task | None = None
room_list: dict = {}
_archive: ArchiveRuntime | None = None

# ── 通知推送 ─────────────────────────────────────────────────────────────────────────

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
        except ActionFailed as e:
            await log_message(f"[mycard_subscriber] _send_notifications ActionFailed: {e}")
            retcode = e.info.get("retcode") if hasattr(e, "info") else getattr(e, "retcode", None)
            message = e.info.get("message") if hasattr(e, "info") else getattr(e, "message", None)
            if retcode == 1200:
                m = re.search(r'"result":\s*(\d+)', message or "")
                if m and int(m.group(1)) in (16, 110):
                    unsubscribe_all(usertype, qq)
                    await log_message(f"[mycard_subscriber] Auto-unsubscribed invalid subscription: {usertype} {qq}")
        except Exception as e:
            await log_message(f"[mycard_subscriber] _send_notifications error: {e}")


# ── 对局事件处理 ─────────────────────────────────────────────────────────────────────

async def handle_create_event(bot: Bot, player_ids: list):
    try:
        subscribe_list = get_subscribe_list()
        for i, player_id in enumerate(player_ids):
            if player_id in subscribe_list:
                message = f"您关注的{player_id}已开始对局，对手id：{player_ids[1-i]}。"
                asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message, "private"))
        if player_ids[0] in subscribe_list and player_ids[1] in subscribe_list:
            subscribers_0 = set(subscribe_list.get(player_ids[0], []))
            subscribers_1 = set(subscribe_list.get(player_ids[1], []))
            common_subscribers = subscribers_0 & subscribers_1
            message = f"您关注的{player_ids[0]}和{player_ids[1]}已开始对局。"
            asyncio.create_task(_send_notifications(bot, list(common_subscribers), message, "group"))
        
    except Exception as e:
        await log_message(f"[mycard_subscriber] handle_create_event error: {e}")


async def handle_delete_event(bot: Bot, room_id):
    try:
        subscribe_list = get_subscribe_list()
        if room_id not in room_list:
            await message_superusers(f"房间不在列表中：{room_id}")
            return
        
        player_ids = room_list[room_id]
        del room_list[room_id]

        if len(player_ids) != 2:
            await message_superusers(f"房间玩家数异常：{room_id}，{player_ids}")
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
            await log_message(f"[mycard_subscriber] 获取最新记录失败，已重试3分钟：{player_ids[0]} vs {player_ids[1]}")
            return

        pt_deltas = [rec["pta"] - rec["pta_ex"], rec["ptb"] - rec["ptb_ex"]]
        pt_strs = [f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}" for delta in pt_deltas]

        if not _archive and await get_notify_enabled():
            await message_superusers(f"对局已完成：{player_ids[0]}({pt_strs[0]}) vs {player_ids[1]}({pt_strs[1]})")

        if rec["isfirstwin"]:
            for i, player_id in enumerate(player_ids):
                if player_id in subscribe_list:
                    if pt_deltas[i] > 0:
                        message = f"您关注的{player_id}成功拿下首赢！pt变动：{pt_strs[i]}。"
                        asyncio.create_task(_send_notifications(bot, subscribe_list.get(player_id, []), message, "both"))
        
    except Exception as e:
        await log_message(f"[mycard_subscriber] handle_delete_event error: {e}")


async def process_mycard_event(bot: Bot, payload: dict):
    if _archive:
        try:
            await _archive.process(payload)
        except Exception as exc:
            logger.error(f"[mycard_archive] 房间事件处理失败：{type(exc).__name__}")
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


# ── WebSocket 监控 ────────────────────────────────────────────────────────────────────

async def ws_runner(bot: Bot):
    """带指数退避重连的 WS 监听循环。"""
    backoff = 1
    max_backoff = 60
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                    await log_message("[mycard_subscriber] WS connected.")
                    backoff = 1  # 连接成功后重置退避
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                # await log_message(f"[mycard raw] {msg.data}")
                                await process_mycard_event(bot, data)
                            except Exception:
                                await log_message(f"[mycard_subscriber] JSON parse error: {msg.data}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            await log_message("[mycard_subscriber] WS connection closed or error.")
                            break
        except Exception as e:
            await log_message(f"[mycard_subscriber] WS error: {str(e)}")
        
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def ws_status_check() -> bool:
    """返回 WS 任务是否正在运行（供 base.py 状态查询使用）。"""
    return bool(_ws_task and not _ws_task.done())


# ── 启动与关闭钉子 ───────────────────────────────────────────────────────────────────

driver = get_driver()
@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    global _ws_task, _archive
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await log_message(f"[mycard_subscriber] Exception occurred while canceling ws task: {e}")
        await log_message("[mycard_subscriber] MyCard monitor canceled old task.")
    
    async def send_archive_message(recipient: str, text: str):
        await bot.send_private_msg(user_id=int(recipient), message=MessageSegment.text(text))

    if settings.mycard_capture_enabled:
        if not settings.superusers or not all(uid.isdigit() for uid in settings.superusers):
            logger.error("[mycard_archive] 未配置有效的 SUPERUSERS，归档采集未启动")
        elif _archive:
            # QQ 重连只更换投递连接，不中断正在进行的观战。
            _archive.send_message = send_archive_message
        else:
            try:
                _archive = ArchiveRuntime(settings.mycard_archive_dir, settings.mycard_credentials_file,
                                          settings.superusers, send_archive_message,
                                          concurrency=settings.mycard_capture_concurrency,
                                          join_interval=settings.mycard_join_interval,
                                          rank_filter=RankFilter())
                _archive.start()
            except Exception as exc:
                logger.error(f"[mycard_archive] 归档启动失败：{type(exc).__name__}")
                _archive = None
    _ws_task = asyncio.create_task(ws_runner(bot))
    await log_message("[mycard_subscriber] MyCard monitor started.")

@driver.on_shutdown
async def _on_shutdown():
    global _ws_task, _archive
    if _ws_task and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await log_message(f"[mycard_subscriber] Exception occurred while canceling ws task on shutdown: {e}")
        await log_message("[mycard_subscriber] MyCard monitor canceled on shutdown.")
    _ws_task = None
    if _archive:
        await _archive.close()
        _archive = None


# ── 管理员命令 ─────────────────────────────────────────────────────────────────────
# 用法：切换通知

notify_switch = on_cmd("切换通知", permission=SUPERUSER)

@notify_switch.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if await get_notify_enabled():
        await set_notify_enabled(False)
        await notify_switch.finish("已关闭MyCard对局通知。")
    else:
        await set_notify_enabled(True)
        await notify_switch.finish("已开启MyCard对局通知。")


archive_status = on_cmd("MyCard采集状态", permission=SUPERUSER, priority=5)
archive_files = on_cmd("MyCard文件", permission=SUPERUSER, priority=5)
archive_rules = on_cmd("MyCard规则检查", permission=SUPERUSER, priority=5)


@archive_status.handle()
async def _archive_status(event: PrivateMessageEvent):
    if not _archive:
        await archive_status.finish("MyCard 归档未启用，请检查 MYCARD_CAPTURE_ENABLED 与管理员配置。")
    await archive_status.finish(await _archive.status())


@archive_rules.handle()
async def _archive_rules(event: PrivateMessageEvent):
    if not _archive:
        await archive_rules.finish("MyCard 归档未启用。")
    try:
        rules = await asyncio.to_thread(read_rules, _archive.store.rules_path)
    except (OSError, ValueError):
        await archive_rules.finish("规则文件格式错误，请检查 deck-rules.json；原始文件与卡组链接仍会保留。")
    await archive_rules.finish(f"规则有效，共 {len(rules)} 条。规则在每场结束时重新读取，无需重启。")


@archive_files.handle()
async def _archive_files(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    if not _archive:
        await archive_files.finish("MyCard 归档未启用。")
    try:
        path = await asyncio.to_thread(_archive.store.bundle, args.extract_plain_text().strip())
    except ValueError as exc:
        await archive_files.finish(str(exc))
    except OSError:
        await archive_files.finish("归档文件暂时无法读取，请检查服务器存储后重试。")
    try:
        data = await asyncio.to_thread(path.read_bytes)
        if len(data) > 8 * 1024 * 1024:
            await archive_files.finish("归档超过私聊传输上限，文件仍保存在服务器归档目录。")
        # NapCat 支持 base64 文件，避免 Bot 与 QQ 端不共享文件系统导致上传失败。
        await bot.upload_private_file(user_id=event.user_id, file="base64://" + base64.b64encode(data).decode("ascii"),
                                      name=f"mycard-{path.parent.name}.zip")
    except ActionFailed:
        await archive_files.finish("文件发送失败，归档已保留；请确认可以与 Bot 私聊后重试。")
    await archive_files.finish("已发送原始录像、各小局 YDK 和含部分 Side 的合并 YDK。")
