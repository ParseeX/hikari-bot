"""C2C card replies; optional price backend must be explicitly injected."""
from .output_templates import present, arrange
import asyncio
import re
from typing import Protocol
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from nonebot.adapters.qq.models import MessageKeyboard
from hikari_bot.services.card_text import format_card_effect
from .delivery import TEXT_CHARS, context
from .preview import report
EFFECT_COMMANDS = {'效果', '效果查询', '游戏王效果'}

class PriceBackend(Protocol):
    """Implement with injected bridge client and independent cache, never dotenv.

    The implementation owns 180-second rarity/version sessions keyed by app,
    C2C user and stable card identifier. It reuses ComparisonService, group_rarities,
    rarity_prompt and format_comparison. Legacy catalog is read-only; optional
    version-cache writes must go to an independent srbs database.
    """

    async def start(self, info: dict, user_id: str) -> str:
        ...

def escape_markdown(value):
    text = str(value or '')
    text = re.sub('](?=\\s*[([])', ']\u200b', text)
    text = re.sub('(?m)^(\\s*\\[[^\\]\\n]+])(?=\\s*:)', lambda m: m[1] + '\u200b', text)
    text = re.sub('<(?=[A-Za-z/!@])', '<\u200b', text)
    text = re.sub('([\\\\`*_~])', '\\\\\\1', text)
    text = re.sub('(?m)^(?=\\s*(?:#{1,6}\\s|>\\s|[-+]\\s|\\d+[.)]\\s))', '\u200b', text)
    return text

def command_button(key, label, command, user_id, style=0):
    return {'id': key, 'render_data': {'label': label, 'visited_label': label, 'style': style}, 'action': {'type': 1, 'permission': {'type': 0, 'specify_user_ids': [user_id]}, 'data': command, 'unsupport_tips': '请在支持按钮的 QQ 客户端打开'}}

def primary_buttons(code, user_id, *, images, prices):
    return [command_button(key + '_' + code, label, f'{command} {code}', user_id, style) for available, key, label, command, style in ((images, 'image', '查看卡图', '卡图', 1), (prices, 'price', '查询卡价', '卡价', 0)) if code and available]

def image_navigation(info, user_id, offset, total, *, prices=False):
    code = stable_code(info)
    buttons=[]
    for enabled,step,label,role in ((offset>0,-1,'上一张','previous'),(offset+1<total,1,'下一张','next')):
        if enabled: buttons.append((role,command_button(f'art_{code}_{offset+step}',label,f'卡图 {code} {offset+step}',user_id)))
    buttons.extend((b['id'].split('_')[0],b) for b in primary_buttons(code,user_id,images=True,prices=prices))
    text=present('art.page',{'name':info.get('cn_name') or info.get('jp_name') or '卡图','index':offset+1,'total':total})
    return output_message(text,arrange('art.page',buttons))

def stable_code(info):
    value = str(info.get('passcode') or info.get('temporary_id') or info.get('id') or '')
    return value if value.isascii() and value.isdigit() and (0 < int(value) <= 2147483647) else None

def output_message(text,rows):
    segment=MessageSegment.markdown(str(text)) if text.mode=='markdown' else MessageSegment.text(str(text))
    message=Message(segment)
    if rows: message+=MessageSegment.keyboard(MessageKeyboard.model_validate({'content':{'rows':rows}}))
    return message

def card_values(info):
    texts=info.get('text') or {}
    jp=info.get('jp_name') or ''
    official=jp or info.get('en_name') or ''
    if jp and info.get('jp_ruby'): official+='（'+info['jp_ruby']+'）'
    return {'name':info.get('cn_name') or jp or info.get('en_name') or '卡片',
            'official':official,'types':texts.get('types') or '',
            'pendulum':('## 灵摆效果\n'+escape_markdown(texts['pdesc'])+'\n\n***\n\n') if texts.get('pdesc') else '',
            'effect':texts.get('desc') or '暂无效果资料。'}

def card_message(info, user_id, *, images=False, prices=False):
    content=present('effect.card',card_values(info))
    buttons=primary_buttons(stable_code(info),user_id,images=images,prices=prices)
    rows=arrange('effect.card',[(b['id'].split('_')[0],b) for b in buttons])
    return output_message(content,rows),present('effect.card',card_values(info),plain=True),content

class CardActions:

    def __init__(self, catalog, *, image_loader=None, price_backend=None):
        self.catalog, self.image_loader, self.price_backend = (catalog, image_loader, price_backend)

    async def handle(self, command, argument, config, bot, event, pager):
        if not isinstance(event, C2CMessageCreateEvent) or len(config.users) != 1 or (not config.allows('c2c', event.get_user_id())):
            return False
        price_commands = {'卡价', '卡价查询', '价格查询', '日版价格查询', '日版卡价查询'}
        known = EFFECT_COMMANDS | {'卡图'} | price_commands
        if command not in known:
            if self.price_backend is not None and self.price_backend.active(event.get_user_id()) and (not argument) and (command not in {'继续', 'ping', 'help', '帮助', '卡表', '样式预览'}):
                try:
                    response = await asyncio.wait_for(self.price_backend.choose(command, event.get_user_id()), timeout=95)
                except TimeoutError:
                    response = present('price.prices_9')
                await pager.send(bot, event, response)
                return True
            return False
        try:
            if not argument or len(argument) > 200:
                await pager.send(bot, event, present('art.card_actions_1', {'value1': f'{command}'}))
                return True
            if self.catalog is None:
                await pager.send(bot, event, present('art.card_actions_2', {}))
                return True
            offset = 0
            query = argument
            rarity = prefix = None
            if command in price_commands:
                from hikari_bot.features.cardrush.parsing import parse_price_query
                query, rarity, prefix = parse_price_query(argument)
            if command == '卡图':
                parts = argument.split()
                if len(parts) == 2 and all((p.isascii() and p.isdigit() for p in parts)):
                    query, offset = (parts[0], int(parts[1]))
            search_options = {'fuzzy': True} if command in price_commands else {}
            info = await asyncio.wait_for(asyncio.to_thread(self.catalog.search, query, **search_options), timeout=8)
            if not info:
                await pager.send(bot, event, present('art.card_actions_3', {}))
            elif command in EFFECT_COMMANDS:
                rich, plain, content = card_message(info, event.get_user_id(), images=self.image_loader is not None, prices=self.price_backend is not None)
                if len(content) <= TEXT_CHARS:
                    await pager.send(bot, event, plain, preview=rich, report=report)
                else:
                    await pager.send(bot, event, plain)
            elif command in price_commands:
                if self.price_backend is None:
                    await pager.send(bot, event, present('art.card_actions_4', {}))
                else:
                    response = await asyncio.wait_for(self.price_backend.start(info, event.get_user_id(), rarity=rarity, prefix=prefix, japanese=command in {'日版价格查询', '日版卡价查询'}), timeout=95)
                    await pager.send(bot, event, response)
            else:
                await self.images(info, offset, bot, event, pager)
        except (TimeoutError, OSError, ValueError):
            report('card_query_failed')
            await pager.send(bot, event, present('art.card_actions_5', {}))
        except Exception as exc:
            report('card_reply_failed', exc)
        return True

    async def images(self, info, offset, bot, event, pager):
        code = stable_code(info)
        if self.image_loader is None or not code:
            await pager.send(bot, event, present('art.card_actions_6', {}))
            return
        ids = await asyncio.wait_for(asyncio.to_thread(self.catalog.image_ids, code), timeout=8)
        if offset < 0 or offset >= len(ids):
            await pager.send(bot, event, present('art.card_actions_7', {}))
            return
        key = context(bot, event)
        async with pager.lock:
            now = pager.clock()
            for old in [k for k, expiry in pager.seen.items() if expiry <= now]:
                del pager.seen[old]
            marker = (*key, event.id)
            if marker in pager.seen or pager.capacity(event, 'c2c') < 2:
                return
            pager.seen[marker] = now + 3600
            while len(pager.seen) > 2048:
                pager.seen.popitem(last=False)
        data = await asyncio.wait_for(self.image_loader(ids[offset]), timeout=30)
        async with pager.lock:
            if pager.capacity(event, 'c2c') < 2:
                return
            payload = MessageSegment.file_image(data) if data and len(data) <= 8 * 1024 * 1024 else present('art.failed',{'index':offset+1})
            await bot.send(event, payload)
            if pager.capacity(event, 'c2c'):
                await bot.send(event, image_navigation(info, event.get_user_id(), offset, len(ids), prices=self.price_backend is not None))

async def existing_art_loader(card_id):
    from hikari_bot.services.card_art import get_image_by_id, normalize_card_art
    data = await get_image_by_id(card_id)
    return await asyncio.to_thread(normalize_card_art, data) if data else None
