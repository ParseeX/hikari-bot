"""保留小型 PDF/文件的名称；其余发送继续交给 SDK。"""
from pathlib import PurePath

from nonebot.adapters.qq import Bot, Message, MessageSegment
from .output_templates import Output


def output_message(message):
    """在群聊、私聊和主动消息入口统一保留展示格式。"""
    if isinstance(message, Output):
        return Message(MessageSegment.markdown(str(message))) if message.mode == 'markdown' else Message(str(message))
    return Message(message)


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
