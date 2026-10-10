"""群聊和私聊的 MyCard 玩家卡组查询。"""
import asyncio
import sqlite3
import time

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER

from hikari_bot.core.commands import on_cmd
from hikari_bot.core.config import settings
from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.queries import Leaderboard, player_text, top_texts
from hikari_bot.services.qq_forward_receipt import confirm_forward, forward_resource


deck_query = on_cmd('卡组查询', priority=5, block=True, permission=SUPERUSER)
top_decks = on_cmd('前排卡组信息', priority=5, block=True)
leaderboard = Leaderboard()


def library():
    return DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3')


@deck_query.handle()
async def query_player(event: MessageEvent, args: Message = CommandArg()):
    player = args.extract_plain_text().strip()
    if not player:
        await deck_query.finish('用法：卡组查询 MyCard用户名')
    try:
        version = await asyncio.to_thread(library().latest, player)
        text = await asyncio.to_thread(player_text, player, version)
    except sqlite3.Error:
        await deck_query.finish('卡组库暂不可用')
    except ValueError:
        await deck_query.finish('卡组链接生成失败')
    await deck_query.finish(MessageSegment.text(text))


@top_decks.handle()
async def query_top(bot: Bot, event: MessageEvent):
    try:
        texts = await top_texts(library(), leaderboard)
    except sqlite3.Error:
        await top_decks.finish('卡组库暂不可用')
    except Exception as exc:
        logger.warning(f'[mycard_decks] 前排查询失败：{type(exc).__name__}')
        await top_decks.finish('前排卡组查询失败，请稍后重试')
    # 十名一节点，总共十个节点、一次合并转发；文本转义避免用户名被识别为 CQ 指令。
    nodes = [MessageSegment.node_custom(user_id=int(bot.self_id), nickname='前排卡组信息',
                                        content=Message(MessageSegment.text('\n\n'.join(texts[i:i + 10]))))
             for i in range(0, len(texts), 10)]
    started = time.time()
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api('send_group_forward_msg', group_id=event.group_id, messages=nodes)
        else:
            await bot.call_api('send_private_forward_msg', user_id=event.user_id, messages=nodes)
    except Exception as exc:
        resource = forward_resource(exc)
        if resource:
            target = {'group_id': event.group_id} if isinstance(event, GroupMessageEvent) else {'user_id': event.user_id}
            if await confirm_forward(bot, resource=resource, since=started, **target):
                logger.info('[mycard_decks] 转发回执异常，历史记录已确认发送')
                return
            logger.warning('[mycard_decks] 转发资源已上传，发送结果未确认')
            await top_decks.finish('QQ 未确认发送结果，请先查看聊天记录')
        info = getattr(exc, 'info', {})
        code = info.get('retcode') if isinstance(info, dict) else None
        detail = type(exc).__name__ + (f'，retcode={code}' if isinstance(code, int) else '')
        logger.warning(f'[mycard_decks] 合并转发失败：{detail}')
        await top_decks.finish(f'卡组消息发送失败（{detail}）')
