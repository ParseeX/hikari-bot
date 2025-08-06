import base64
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from hikari_bot.utils.constants import *

help_pic = os.path.join(RESOURCES_DIR, 'help.png')

help = on_command("帮助", priority=5)
@help.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    with open(help_pic, "rb") as f:
        image_data = f.read()
    image_base64 = base64.b64encode(image_data).decode("utf-8")
    await help.finish(Message([MessageSegment.image(f"base64://{image_base64}")]))