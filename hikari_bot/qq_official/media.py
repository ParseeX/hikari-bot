"""保留小型 PDF/文件的名称；其余发送继续交给 SDK。"""
from pathlib import PurePath

from nonebot.adapters.qq import Bot, Message, MessageSegment
from .output_templates import Output, present
from nonebot.adapters.qq.exception import ActionFailed


def output_message(message):
    """在群聊、私聊和主动消息入口统一保留展示格式。"""
    if isinstance(message, Output):
        return Message(MessageSegment.markdown(str(message))) if message.mode == 'markdown' else Message(str(message))
    return Message(message)


async def send_formatted(bot, event, message, pager, *, can_continue=True):
    """明确拒绝 Markdown 时只报告失败，保留待发内容和原格式。"""
    try:
        await bot.send(event, message)
    except ActionFailed as exc:
        markdown = (isinstance(message, Output) and message.mode == 'markdown') or (isinstance(message, Message) and bool(message['markdown']))
        if not (markdown and exc.status_code in {400, 403}):
            raise
        from .delivery import context
        key = context(bot, event)
        if key and pager.capacity(event, key[1]):
            code = exc.code if type(exc.code) is int and 0 <= exc.code <= 99999999 else '未知'
            # 错误说明本身固定普通文字，避免再次请求刚被拒绝的格式。
            await bot.send(event, str(present('paging.markdown_failed',
                {'status': exc.status_code, 'code': code,
                 'recovery': '请调整文案后重新查询，或回复“继续”按原格式重试。' if can_continue else '请调整文案后重新查询。'}, plain=True)))
        return False
    return True


class NamedFileBot:
    def __init__(self, bot):
        self.bot = bot

    def __getattr__(self, name):
        return getattr(self.bot, name)

    @staticmethod
    def _extract_qq_media(message):
        values = Bot._extract_qq_media(message)
        files = message['file_file']
        if files:
            name = files[-1].data.get('file_name')
            if name:
                if PurePath(name).name != name or '/' in name or '\\' in name or len(name) > 128:
                    raise ValueError('附件文件名无效')
                # SDK 仅为 >10 MiB 设置 file_name；这个字段会选择分片上传。
                values['file_name'] = name
        return values

    async def send_to_c2c(self, **kwargs):
        if 'message' in kwargs:
            kwargs['message'] = output_message(kwargs['message'])
        return await Bot.send_to_c2c(self, **kwargs)

    async def send_to_group(self, **kwargs):
        if 'message' in kwargs:
            kwargs['message'] = output_message(kwargs['message'])
        return await Bot.send_to_group(self, **kwargs)

    async def send(self, event, message):
        message = output_message(message)
        if Message(message)['file_file']:
            return await Bot.send(self, event, message)
        return await self.bot.send(event, message)
