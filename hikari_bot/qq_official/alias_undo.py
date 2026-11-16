"""添加成功后的本人撤销按钮；数据库写入仍由共享服务完成。"""
import asyncio

from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.models import MessageKeyboard

from hikari_bot.services.card_aliases import undo_alias_reply
from .card_actions import command_button, escape_markdown
from .preview import report


async def send_added(bot, event, added):
    button = command_button('alias_undo', '删除该别名', f'撤销别名 {added.receipt}', event.get_user_id())
    message = Message(MessageSegment.markdown(escape_markdown(str(added))))
    message += MessageSegment.keyboard(MessageKeyboard.model_validate({'content': {'rows': [{'buttons': [button]}]}}))
    try:
        await asyncio.wait_for(bot.send(event, message), timeout=20)
    except Exception as error:
        # 添加已完成；按钮失败不能误报保存失败，也不自动重新添加。
        report('alias_undo_button_failed', error)
        await bot.send(event, f'{added}\n删除按钮发送失败，可用：删除别名 {added.code} {added.alias}')


def undo_handler(catalog):
    async def execute(bot, event, receipt):
        await bot.send(event, await undo_alias_reply(catalog, receipt))
    return execute
