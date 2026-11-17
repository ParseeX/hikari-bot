"""群聊与私聊共用卡片文案；可选卡价后端必须显式注入。"""
from .output_templates import present, arrange
import asyncio
import re
from typing import Protocol
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent, GroupAtMessageCreateEvent
from nonebot.adapters.qq.models import MessageKeyboard
from hikari_bot.services.card_catalog import AmbiguousCardName
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

def command_button(key, label, command, user_id, style=0, *, shared=False):
    permission = {'type': 2} if shared else {'type': 0, 'specify_user_ids': [user_id]}
    return {'id': key, 'render_data': {'label': label, 'visited_label': label, 'style': style}, 'action': {'type': 1, 'permission': permission, 'data': command, 'unsupport_tips': '请在支持按钮的 QQ 客户端打开'}}

def primary_buttons(code, user_id, *, images, prices, effects=False):
    return [command_button(key + '_' + code, label, f'{command} {code}', user_id, style, shared=True) for available, key, label, command, style in ((effects, 'effect', '查看效果', '效果', 1), (images, 'image', '查看卡图', '卡图', 1), (prices, 'price', '查询卡价', '卡价', 0)) if code and available]

def image_navigation(info, user_id, offset, total, *, prices=False, buttons=True):
    code = stable_code(info)
    available_buttons=[]
    for enabled,step,label,role in ((offset>0,-1,'上一张','previous'),(offset+1<total,1,'下一张','next')):
        if enabled: available_buttons.append((role,command_button(f'art_{code}_{offset+step}',label,f'卡图 {code} {offset+step}',user_id,shared=True)))
    available_buttons.extend((b['id'].split('_')[0],b) for b in primary_buttons(code,user_id,images=True,prices=prices,effects=True))
    navigation = present('art.next_hint', {'command': f'卡图 {code} {offset+1}'}) if offset+1 < total else present('art.last_hint')
    text=present('art.page',{'name':info.get('cn_name') or info.get('jp_name') or '卡图','index':offset+1,'total':total,'navigation':navigation})
    return output_message(text,arrange('art.page',available_buttons) if buttons else [])

def stable_code(info):
    value = str(info.get('passcode') or info.get('temporary_id') or info.get('id') or '')
    return value if value.isascii() and value.isdigit() and (0 < int(value) <= 2147483647) else None

def output_message(text,rows):
    segment=MessageSegment.markdown(str(text)) if text.mode=='markdown' else MessageSegment.text(str(text))
    message=Message(segment)
    if rows and text.mode == 'markdown': message+=MessageSegment.keyboard(MessageKeyboard.model_validate({'content':{'rows':rows}}))
    return message

def card_values(info):
    texts=info.get('text') or {}
    jp=info.get('jp_name') or ''
    official=jp or info.get('en_name') or ''
    reading=(info.get('jp_ruby') or '').strip() if jp else ''
    if jp and info.get('jp_ruby'): official+='（'+info['jp_ruby']+'）'
    return {'name':info.get('cn_name') or jp or info.get('en_name') or '卡片',
            'official':official,'types':texts.get('types') or '',
            'original':('> '+escape_markdown(jp)) if jp else escape_markdown(info.get('en_name') or ''),
            # 假名与日文名连续两行，共用同一个引用块。
            'reading':''.join('> '+escape_markdown(line)+'\n' for line in reading.splitlines()),
            'pendulum':(escape_markdown(texts['pdesc'])+'\n\n***\n\n') if texts.get('pdesc') else '',
            'effect':texts.get('desc') or '暂无效果资料。', 'body':format_card_effect(info)}

def card_message(info, user_id, *, images=False, prices=False):
    content=card_content('effect.card',info)
    buttons=primary_buttons(stable_code(info),user_id,images=images,prices=prices)
    rows=arrange('effect.card',[(b['id'].split('_')[0],b) for b in buttons])
    return output_message(content,rows),format_card_effect(info),content


def card_text(info):
    """所有会话和长短回复共用同一条效果文案及格式。"""
    return card_content('effect.card', info)


def card_content(key, info):
    values = card_values(info)
    content = present(key, values)
    if content.mode == 'text':
        values['original'] = info.get('jp_name') or info.get('en_name') or ''
        if values['reading']:
            values['reading'] = info['jp_ruby'].strip() + '\n'
        if values['pendulum']:
            values['pendulum'] = info['text']['pdesc'] + '\n---------------\n'
        content = present(key, values)
    return content

class CardActions:

    def __init__(self, catalog, *, image_loader=None, price_backend=None, result_delivery=None, price_history=None):
        from .alias_choices import AliasChoices
        self.catalog, self.image_loader, self.price_backend = (catalog, image_loader, price_backend)
        self.result_delivery = result_delivery
        self.price_choices = AliasChoices(None, page_command='罕贵页')
        self.price_history = price_history

    async def send_price(self, bot, event, response, pager):
        keyboard = self.price_history.quote_keyboard(bot, event, response) if self.price_history else None
        await pager.send(bot, event, response, keyboard=keyboard)

    async def handle(self, command, argument, config, bot, event, pager):
        group = isinstance(event, GroupMessageCreateEvent)
        kind = ('group_at' if isinstance(event, GroupAtMessageCreateEvent) else 'group') if group else 'c2c'
        if not isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)) or not config.allows(kind, event.get_user_id(), event.group_openid if group else ''):
            return False
        session = context(bot, event)
        if command in {'罕贵选择', '罕贵页'}:
            if getattr(bot, 'interaction', None) is None:
                return True
            if command == '罕贵页':
                await self.price_choices.handle(bot, event, command, argument)
            elif self.price_backend is not None and re.fullmatch(r'[0-9]{1,16} [0-9]{1,6}', argument):
                generation, index = argument.split()
                try:
                    response = await asyncio.wait_for(self.price_backend.choose(
                        index, session, expected_generation=int(generation)), timeout=95)
                except TimeoutError:
                    response = present('price.prices_9')
                await self.send_price(bot, event, response, pager)
            return True
        command = {'卡图查询': '卡图', '游戏王卡图': '卡图', '查卡图': '卡图', '查效果': '效果'}.get(command, command)
        price_commands = {'卡价', '卡价查询', '价格查询', '日版价格查询', '日版卡价查询'}
        known = EFFECT_COMMANDS | {'卡图'} | price_commands
        if command not in known:
            if self.price_backend is not None and self.price_backend.active(session) and (not argument) and command.lower() not in {'ping', 'help', 'log', 'status', 'version', 'reload', 'reboot'} and re.fullmatch(r'(?:[0-9]{1,3}|[A-Za-z+]{1,12}|取消|退出)', command):
                try:
                    response = await asyncio.wait_for(self.price_backend.choose(command, session), timeout=95)
                except TimeoutError:
                    response = present('price.prices_9')
                await self.send_price(bot, event, response, pager)
                return True
            return False
        try:
            if not argument or len(argument) > 200:
                await pager.send(bot, event, present('art.card_actions_1', {'value1': f'{command}'}))
                return True
            if self.catalog is None:
                await pager.send(bot, event, present('art.card_actions_2', {}))
                return True
            offset = None
            query = argument
            rarity = prefix = None
            if command in price_commands:
                from hikari_bot.features.cardrush.parsing import parse_price_query
                query, rarity, prefix = parse_price_query(argument)
            if command == '卡图':
                parts = argument.split()
                if len(parts) == 2 and all((p.isascii() and p.isdigit() for p in parts)):
                    query, offset = (parts[0], int(parts[1]))
            info = await asyncio.wait_for(asyncio.to_thread(self.catalog.search, query, fuzzy=True), timeout=8)
            if not info:
                await pager.send(bot, event, present('art.card_actions_3', {}))
            elif command in EFFECT_COMMANDS:
                rich, _, content = card_message(info, event.get_user_id(), images=self.image_loader is not None, prices=self.price_backend is not None)
                keyboards = rich['keyboard']
                keyboard = keyboards[0] if keyboards else None
                if len(content) <= TEXT_CHARS and content.mode == 'markdown':
                    await pager.send(bot, event, content, preview=rich, report=report, allow_text_fallback=False, keyboard=keyboard)
                else:
                    await pager.send(bot, event, content, keyboard=keyboard)
            elif command in price_commands:
                if self.price_backend is None:
                    await pager.send(bot, event, present('art.card_actions_4', {}))
                else:
                    response = await asyncio.wait_for(self.price_backend.start(info, session, rarity=rarity, prefix=prefix, japanese=command in {'日版价格查询', '日版卡价查询'}), timeout=95)
                    if getattr(response, 'key', None) == 'price.prompt' and response.mode == 'markdown':
                        selection = self.price_backend.selection_options(session)
                        if selection:
                            async with pager.lock:
                                pager.pending.pop(session, None)
                            generation, rarities = selection
                            await self.price_choices.offer(bot, event,
                                [(name, f'罕贵选择 {generation} {index}') for index, name in enumerate(rarities, 1)],
                                prompt=str(response))
                    else:
                        await pager.send(bot, event, response)
            else:
                await self.images(info, offset, bot, event, pager)
        except AmbiguousCardName as exc:
            await pager.send(bot, event, str(exc))
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
        if offset is None and ids:
            await self.all_images(info, ids, bot, event, pager)
            return
        if not ids or offset < 0 or offset >= len(ids):
            await pager.send(bot, event, present('art.card_actions_7', {}))
            return
        key = context(bot, event)
        async with pager.lock:
            now = pager.clock()
            for old in [k for k, expiry in pager.seen.items() if expiry <= now]:
                del pager.seen[old]
            marker = (*key, event.id)
            if marker in pager.seen or pager.capacity(event, key[1]) < 2:
                return
            pager.seen[marker] = now + 3600
            while len(pager.seen) > 2048:
                pager.seen.popitem(last=False)
        data = await asyncio.wait_for(self.image_loader(ids[offset]), timeout=30)
        async with pager.lock:
            if pager.capacity(event, key[1]) < 2:
                return
            payload = MessageSegment.file_image(data) if data and len(data) <= 8 * 1024 * 1024 else present('art.failed',{'index':offset+1})
            await bot.send(event, payload)
            if pager.capacity(event, key[1]):
                from .media import send_formatted
                await send_formatted(bot, event, image_navigation(info, event.get_user_id(), offset, len(ids),
                    prices=self.price_backend is not None), pager, can_continue=False)

    async def all_images(self, info, ids, bot, event, pager):
        """默认 Markdown 单条图集；主动选择普通文字时保留原媒体分页。"""
        from hikari_bot.services.platform import Result, Media
        from .markdown_image import MarkdownGallery, GALLERY_MARKER
        from .results import ResultDelivery
        from .media import send_formatted
        gallery = present('art.gallery', {'name': info.get('cn_name') or info.get('jp_name') or '卡图',
                                         'total': len(ids), 'images': GALLERY_MARKER})
        key = context(bot, event)
        if key is None:
            return
        async with pager.lock:
            now = pager.clock()
            for old in [k for k, expiry in pager.seen.items() if expiry <= now]:
                del pager.seen[old]
            marker = (*key, event.id)
            if marker in pager.seen or not pager.capacity(event, key[1]):
                return
            pager.seen[marker] = now + 3600
            while len(pager.seen) > 2048:
                pager.seen.popitem(last=False)
        semaphore = asyncio.Semaphore(3)

        async def load(index, card_id):
            async with semaphore:
                try:
                    data = await asyncio.wait_for(self.image_loader(card_id), timeout=30)
                    if data and len(data) <= (10_000_000 if gallery.mode == 'markdown' else 8 * 1024 * 1024):
                        return Media('image', data)
                except (TimeoutError, OSError, ValueError):
                    pass
                return present('art.failed', {'index': index})

        parts = await asyncio.gather(*(load(index, card_id) for index, card_id in enumerate(ids, 1)))
        if gallery.mode == 'markdown':
            buttons = primary_buttons(stable_code(info), event.get_user_id(), images=False,
                                      prices=self.price_backend is not None, effects=True)
            rows = arrange('art.gallery', [(b['id'].split('_')[0], b) for b in buttons])
            keyboard = MessageSegment.keyboard(MessageKeyboard.model_validate({'content': {'rows': rows}})) if rows else None
            await send_formatted(bot, event, MarkdownGallery(str(gallery),
                tuple(part.data if isinstance(part, Media) else str(part) for part in parts), keyboard), pager, can_continue=False)
            return
        prefix, _, suffix = str(gallery).partition(GALLERY_MARKER)
        parts = ([prefix] if prefix else []) + parts + ([suffix] if suffix else [])
        if self.result_delivery is None:
            self.result_delivery = ResultDelivery(pager)
        try:
            await self.result_delivery.send(bot, event, Result(media=tuple(parts)), combine_last_caption=bool(suffix))
        except Exception as exc:
            report('card_reply_failed', exc)
            if pager.capacity(event, key[1]):
                await bot.send(event, str(present('art.send_failed', plain=True)))

async def existing_art_loader(card_id):
    from hikari_bot.services.card_art import get_image_by_id, normalize_card_art
    data = await get_image_by_id(card_id)
    return await asyncio.to_thread(normalize_card_art, data) if data else None
