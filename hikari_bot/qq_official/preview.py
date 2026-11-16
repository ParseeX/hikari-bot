"""One fixed, opt-in card preview for the sole allowed C2C user."""
from .output_templates import present, arrange
import asyncio
import json
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from .delivery import TEXT_CHARS
from .diagnostics import exception_metadata
CARD_ID = '14558127'

def report(result, exc=None):
    payload = {'result': result}
    if exc is not None:
        payload['errors'] = exception_metadata(exc)
    print('SRBS_PREVIEW ' + json.dumps(payload, ensure_ascii=True, sort_keys=True), flush=True)

def build_preview(info, user_id):
    from .card_actions import card_content,command_button,output_message
    from hikari_bot.services.card_text import format_card_effect
    content=card_content('preview.card',info)
    buttons=[(key,command_button('ash_'+key,label,command+' '+CARD_ID,user_id,style))
             for key,label,command,style in [('effect','完整效果','效果',1),('passcode','卡密','卡密',0)]]
    return output_message(content,arrange('preview.card',buttons)),format_card_effect(info),content

async def handle_preview(config, bot, event, pager, catalog):
    if not isinstance(event, C2CMessageCreateEvent) or len(config.users) != 1 or (not config.allows('c2c', event.get_user_id())):
        return
    try:
        info = None if catalog is None else await asyncio.wait_for(asyncio.to_thread(catalog.search, CARD_ID), timeout=8)
        if not info:
            await pager.send(bot, event, present('preview.preview_1', {}))
            return
        rich, plain, content = build_preview(info, event.get_user_id())
        if len(content) > TEXT_CHARS:
            report('local_plain_text')
            await pager.send(bot, event, present('preview.long',{'body':str(plain)}))
        else:
            await pager.send(bot, event, plain, preview=rich, report=report)
    except Exception as exc:
        report('reply_failed', exc)
