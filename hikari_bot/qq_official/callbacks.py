"""Private C2C callback handles and event-bound delivery; no credential access."""
from .output_templates import present, Output
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import secrets
import time
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, InteractionCreateEvent
from nonebot.adapters.qq.models import MessageKeyboard
from .preview import report
COMMAND = re.compile('^(效果|卡图|卡价|卡密) ([0-9]{1,10})(?: ([0-9]{1,4}))?$')
PREFIX = 'srbs:'
TTL = 600
ACK_SECONDS = 2.0
BUSINESS_SECONDS = 110

@dataclass
class Action:
    app: str
    user: str
    button_id: str
    command: str
    expires: float
    message_id: str = ''
    ready: bool = False
    claimed: bool = False

class CallbackBot:

    def __init__(self, controller, bot, source, *, interaction=None):
        self.controller, self.bot, self.source = (controller, bot, source)
        self.interaction = interaction

    def __getattr__(self, name):
        return getattr(self.bot, name)

    async def send(self, event, message):
        if event is not self.source:
            raise ValueError('Reply context changed')
        user = event.get_user_id()
        if not self.controller.config.allows('c2c', user):
            raise ValueError('Reply user is not allowed')
        prepared, handles = self.controller.prepare(message, str(self.bot.self_id), user)
        try:
            if self.interaction is None:
                result = await self.bot.send(event, prepared)
            else:
                original = self.interaction
                stamp = datetime.fromisoformat(original.timestamp.replace('Z', '+00:00'))
                age = (self.controller.now() - stamp).total_seconds()
                if not -1 <= age < 3600 or event._reply_seq >= 4:
                    raise ValueError('Interaction reply is expired or exhausted')
                event._reply_seq += 1
                result = await self.bot.send_to_c2c(openid=original.user_openid, message=prepared, event_id=original.event_id or original.id, msg_seq=event._reply_seq)
            message_id = getattr(result, 'id', None)
            if message_id is None and isinstance(result, dict):
                message_id = result.get('id')
            if handles and (not message_id):
                raise ValueError('Callback message was not confirmed')
            for handle in handles:
                action = self.controller.actions.get(handle)
                if action:
                    action.message_id = str(message_id or '')
                    action.ready = True
            if self.interaction or handles:
                report('callback_result_api_accepted' if self.interaction else 'callback_buttons_api_accepted')
            return result
        except BaseException:
            for handle in handles:
                self.controller.actions.pop(handle, None)
            raise

class CallbackController:

    def __init__(self, config, processor, *, clock=time.monotonic, now=None):
        self.config, self.processor, self.clock = (config, processor, clock)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.actions = OrderedDict()
        self.seen = OrderedDict()
        self.tasks = set()
        self.inflight = 0

    def prune(self):
        now = self.clock()
        for handle in [k for k, v in self.actions.items() if v.expires <= now]:
            self.actions.pop(handle, None)
        for key in [k for k, v in self.seen.items() if v <= now]:
            self.seen.pop(key, None)
        while len(self.actions) > 2048:
            self.actions.popitem(last=False)
        while len(self.seen) > 2048:
            self.seen.popitem(last=False)

    def wrap(self, bot, source):
        return CallbackBot(self, bot, source)

    def prepare(self, message, app, user):
        if isinstance(message,Output) and message.mode=='markdown':
            message=MessageSegment.markdown(str(message))
        elif isinstance(message,Output):
            message=str(message)
        prepared = deepcopy(Message(message))
        handles = []
        self.prune()
        try:
            for segment in prepared['keyboard']:
                keyboard = segment.data['keyboard'].model_dump(exclude_none=True)
                for row in keyboard.get('content', {}).get('rows', []):
                    for button in row['buttons']:
                        action = button['action']
                        command = action.get('data', '')
                        match = COMMAND.fullmatch(command)
                        if action.get('type') != 1 or not match or (not 0 < int(match[2]) <= 2147483647) or (match[3] is not None and match[1] != '卡图'):
                            raise ValueError('Unsupported callback action')
                        handle = PREFIX + secrets.token_urlsafe(18)
                        self.actions[handle] = Action(app, user, button['id'], command, self.clock() + TTL)
                        handles.append(handle)
                        button['action'] = {'type': 1, 'data': handle, 'permission': {'type': 0, 'specify_user_ids': [user]}, 'unsupport_tips': '当前客户端不支持回调；可手动输入原查询命令'}
                segment.data['keyboard'] = MessageKeyboard.model_validate(keyboard)
            self.prune()
            return (prepared, handles)
        except BaseException:
            for handle in handles:
                self.actions.pop(handle, None)
            raise

    def trusted(self, bot, event):
        return isinstance(event, InteractionCreateEvent) and event.type == 11 and (event.application_id == str(bot.self_id) == self.config.app_id) and (event.chat_type == 2) and (event.scene == 'c2c') and (not any([event.group_openid, event.group_member_openid, event.guild_id, event.channel_id])) and isinstance(event.user_openid, str) and self.config.allows('c2c', event.user_openid)

    async def ack(self, bot, event, code, timeout=ACK_SECONDS):
        try:
            await asyncio.wait_for(bot.put_interaction(interaction_id=event.id, code=code), timeout=timeout)
            report('callback_ack_accepted' if code == 0 else 'callback_rejected')
            return True
        except Exception as exc:
            report('callback_ack_failed', exc)
            return False

    def source(self, event, command):
        return C2CMessageCreateEvent.model_validate({'id': 'callback:' + event.id, 'content': command, 'timestamp': event.timestamp, 'author': {'id': event.user_openid, 'user_openid': event.user_openid}})

    async def hint(self, bot, event, text):
        if not self.trusted(bot, event):
            return
        source = self.source(event, '')
        try:
            await asyncio.wait_for(CallbackBot(self, bot, source, interaction=event).send(source, text), timeout=5)
        except Exception as exc:
            report('callback_hint_failed', exc)

    async def handle(self, bot, event):
        if not isinstance(event, InteractionCreateEvent) or event.type != 11:
            return
        if event.application_id != self.config.app_id or str(bot.self_id) != self.config.app_id:
            return
        if not re.fullmatch('[A-Za-z0-9_-]{1,128}', event.id):
            report('callback_identity_invalid')
            return
        self.prune()
        identity = (event.application_id, event.id)
        if identity in self.seen:
            return
        self.seen[identity] = self.clock() + 3600
        if not self.trusted(bot, event):
            await self.ack(bot, event, 4)
            return
        try:
            age = (self.now() - datetime.fromisoformat(event.timestamp.replace('Z', '+00:00'))).total_seconds()
        except (ValueError, TypeError):
            await self.ack(bot, event, 1)
            return
        if not -1 <= age < 2.8:
            report('callback_event_expired')
            await self.hint(bot, event, present('callback.callbacks_1', {}))
            return
        resolved = event.data.resolved
        action = self.actions.get(resolved.button_data)
        valid = action is not None and action.ready and (action.app == event.application_id) and (action.user == event.user_openid) and (action.button_id == resolved.button_id) and (not resolved.message_id or resolved.message_id == action.message_id)
        if not valid or action.claimed:
            await self.ack(bot, event, 3 if valid else 1)
            await self.hint(bot, event, present('callback.callbacks_2', {}))
            return
        if self.inflight >= 2:
            await self.ack(bot, event, 2)
            return
        action.claimed = True
        self.inflight += 1
        if not await self.ack(bot, event, 0, timeout=min(ACK_SECONDS, 2.8 - max(0, age))):
            self.inflight -= 1
            await self.hint(bot, event, present('callback.callbacks_3', {}))
            return
        task = asyncio.create_task(self.execute(bot, event, action.command))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def execute(self, bot, event, command):
        source = self.source(event, command)
        proxy = CallbackBot(self, bot, source, interaction=event)
        try:
            await asyncio.wait_for(self.processor(proxy, source), timeout=BUSINESS_SECONDS)
        except Exception as exc:
            report('callback_business_failed', exc)
        finally:
            self.inflight -= 1
