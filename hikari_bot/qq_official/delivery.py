"""Lossless text paging within each incoming event's passive reply budget."""
from .output_templates import present, Output
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
from nonebot.adapters.qq.exception import ActionFailed
TEXT_CHARS = 1500
CONTINUE_HINT = '\n（内容未完，回复“继续”接收后续内容。）'
PENDING_SECONDS = 600
LIMITS = {'c2c': (4, 3600), 'group': (5, 300)}

@dataclass
class Pending:
    text: str
    offset: int
    expires: float
    keyboard: object = None

def context(bot, event):
    app = str(getattr(bot, 'self_id', ''))
    if isinstance(event, GroupMessageCreateEvent):
        return (app, 'group', event.group_openid, event.get_user_id())
    if isinstance(event, C2CMessageCreateEvent):
        return (app, 'c2c', event.get_user_id())
    return None

def chunk_end(text, start, size):
    end = min(len(text), start + size)
    if end < len(text):
        newline = text.rfind('\n', start, end)
        if newline >= start:
            end = newline + 1
    return end

class ReplyPager:

    def __init__(self, *, clock=time.monotonic, now=None):
        self.clock = clock
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.pending = OrderedDict()
        self.seen = OrderedDict()
        self.lock = asyncio.Lock()

    def capacity(self, event, kind):
        limit, lifetime = LIMITS[kind]
        stamp = event.timestamp
        if isinstance(stamp, str):
            try:
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except ValueError:
                return 0
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if (self.now() - stamp).total_seconds() >= lifetime or not event.id:
            return 0
        return max(0, limit - event._reply_seq)

    async def send(self, bot, event, text=None, *, preview=None, report=None, allow_text_fallback=True, keyboard=None):
        """text=None continues this user's pending result using a NEW event budget.

        Failed sends are never retried automatically. Their unconfirmed text stays
        pending for an explicit continuation; ambiguous network delivery may duplicate it.
        """
        key = context(bot, event)
        if key is None:
            return
        kind = key[1]
        async with self.lock:
            now = self.clock()
            for old in [k for k, expiry in self.seen.items() if expiry <= now]:
                del self.seen[old]
            marker = (*key, event.id)
            if marker in self.seen or not self.capacity(event, kind):
                return
            self.seen[marker] = now + LIMITS[kind][1]
            while len(self.seen) > 2048:
                self.seen.popitem(last=False)
            for old in [k for k, value in self.pending.items() if value.expires <= now]:
                del self.pending[old]
            if text is not None:
                if not text:
                    return
                self.pending.pop(key, None)
                self.pending[key] = Pending(text, 0, now + PENDING_SECONDS, keyboard)
                while len(self.pending) > 128:
                    self.pending.popitem(last=False)
            state = self.pending.get(key)
            if state is None:
                await bot.send(event, present('paging.delivery_1', {}))
                return
            if preview is not None and not allow_text_fallback:
                from .media import send_formatted
                if not await send_formatted(bot, event, preview, self):
                    return
                self.pending.pop(key, None)
                if report: report('rich_api_accepted', None)
                return
            if preview is not None:
                try:
                    await bot.send(event, preview)
                except Exception as exc:
                    report('rich_failed', exc)
                    if not isinstance(exc, ActionFailed) or exc.status_code not in {400, 403}:
                        raise
                    code = exc.code if type(exc.code) is int and 0 <= exc.code <= 99999999 else '未知'
                    state.text = str(present('paging.rejected',{'status':exc.status_code,'code':code})) + str(state.text)
                else:
                    self.pending.pop(key, None)
                    report('rich_api_accepted', None)
                    return
            hint=str(present('paging.continue'))[:400]
            slots = self.capacity(event, kind)
            for index in range(slots):
                if not self.capacity(event, kind):
                    return
                size = TEXT_CHARS
                if (preview is None and isinstance(state.text, Output)
                        and state.text.key == 'price.result' and state.text.mode == 'markdown'):
                    # 报价的全部版本和末尾曲线按钮必须在同一条消息中，重试也不拆分。
                    size = len(state.text)
                if index == slots - 1 and len(state.text) - state.offset > size:
                    size -= len(hint)
                end = chunk_end(state.text, state.offset, size)
                payload = state.text[state.offset:end]
                if index == slots - 1 and end < len(state.text):
                    if len(payload) + len(hint) > TEXT_CHARS:
                        end = chunk_end(state.text, state.offset, TEXT_CHARS - len(hint))
                        payload = state.text[state.offset:end]
                    payload += hint
                if isinstance(state.text,Output) and preview is None:
                    payload=Output(payload,state.text.key,state.text.mode)
                    if end == len(state.text) and state.text.mode == 'markdown' and state.keyboard is not None:
                        from nonebot.adapters.qq import Message, MessageSegment
                        payload = Message(MessageSegment.markdown(str(payload))) + state.keyboard
                from .media import send_formatted
                if not await send_formatted(bot, event, payload, self):
                    return
                state.offset = end
                state.expires = self.clock() + PENDING_SECONDS
                if end == len(state.text):
                    self.pending.pop(key, None)
                    if preview is not None:
                        report('text_fallback_api_accepted', None)
                    return
