"""按钮绑定原会话；群别名撤销额外允许显式管理员，使用事件额度回复。"""
from .output_templates import present, Output, CARD_NAME_CONFIRMED
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import secrets
import time
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent, GroupAtMessageCreateEvent, InteractionCreateEvent
from nonebot.adapters.qq.models import MessageKeyboard
from .preview import report
COMMAND = re.compile('^(效果|卡图|卡价|卡密|日版价格查询|共界计算) ([0-9]{1,10})(?: (.{1,80}))?$')
PAGE_COMMAND = re.compile(r'^(?:候选页|视频候选页|罕贵页) [A-Za-z0-9_-]{24} [0-9]{1,6}$')
RARITY_COMMAND = re.compile(r'^罕贵选择 [0-9]{1,16} [0-9]{1,6}$')
VIDEO_COMMAND = re.compile(r'^https://www\.bilibili\.com/video/BV[0-9A-Za-z]{10}$')
UNDO_COMMAND = re.compile(r'^撤销别名 ([0-9a-f]{32})$')
PREFIX = 'srbs:'
TTL = 600
ACK_SECONDS = 2.0
BUSINESS_SECONDS = 110
BUSY_HINTS = {'paging.plugin_e278b7fd', 'links.link_parser_ecde216f',
              'document.official_control_617ffdd2', 'paging.media_busy'}

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
    group: str = ''
    admins: frozenset[str] = frozenset()


def supported_command(command):
    if PAGE_COMMAND.fullmatch(command) or UNDO_COMMAND.fullmatch(command) or VIDEO_COMMAND.fullmatch(command) or RARITY_COMMAND.fullmatch(command):
        return True
    match = COMMAND.fullmatch(command)
    if not match or not 0 < int(match[2]) <= 2147483647:
        return False
    suffix = match[3]
    if suffix is None:
        return True
    if match[1] == '卡图':
        return bool(re.fullmatch(r'[0-9]{1,4}', suffix))
    if match[1] in {'卡价', '日版价格查询'}:
        return bool(re.fullmatch(r'[A-Za-z0-9+\-/]+(?: [A-Za-z0-9+\-/]+)?', suffix))
    return False

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
        group = event.group_openid if isinstance(event, GroupMessageCreateEvent) else ''
        if not self.controller.config.allows('group_at' if group else 'c2c', user, group):
            raise ValueError('Reply user is not allowed')
        if isinstance(message, Output) and not self.controller.allow_hint(message.key, str(self.bot.self_id), user, group):
            return
        prepared, handles = self.controller.prepare(message, str(self.bot.self_id), user, group)
        try:
            if self.interaction is None:
                result = await self.bot.send(event, prepared)
            else:
                original = self.interaction
                stamp = datetime.fromisoformat(original.timestamp.replace('Z', '+00:00'))
                age = (self.controller.now() - stamp).total_seconds()
                if not -1 <= age < (300 if group else 3600) or event._reply_seq >= (5 if group else 4):
                    raise ValueError('Interaction reply is expired or exhausted')
                event._reply_seq += 1
                arguments = dict(message=prepared, event_id=original.event_id or original.id, msg_seq=event._reply_seq)
                if group:
                    result = await self.bot.send_to_group(group_openid=group, **arguments)
                else:
                    result = await self.bot.send_to_c2c(openid=original.user_openid, **arguments)
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

    def __init__(self, config, processor, *, clock=time.monotonic, now=None, undo_handler=None):
        self.config, self.processor, self.clock = (config, processor, clock)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.actions = OrderedDict()
        self.seen = OrderedDict()
        self.tasks = set()
        self.inflight = 0
        self.undo_handler = undo_handler
        self.hints = OrderedDict()

    def allow_hint(self, key, app, user, group):
        """同一会话的忙碌/空续页提示，30 秒内各至多一次。"""
        category = 'busy' if key in BUSY_HINTS else 'empty_page' if key == 'paging.delivery_1' else None
        if category is None:
            return True
        now = self.clock()
        for old in [k for k, expiry in self.hints.items() if expiry <= now]:
            self.hints.pop(old, None)
        identity = (app, user, group, category)
        if identity in self.hints:
            return False
        self.hints[identity] = now + 30
        while len(self.hints) > 2048:
            self.hints.popitem(last=False)
        return True

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

    def prepare(self, message, app, user, group=''):
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
                        if action.get('type') != 1 or not supported_command(command):
                            raise ValueError('Unsupported callback action')
                        handle = PREFIX + secrets.token_urlsafe(18)
                        admins = self.config.admins if group and UNDO_COMMAND.fullmatch(command) else frozenset()
                        self.actions[handle] = Action(app, user, button['id'], command, self.clock() + TTL, group=group, admins=admins)
                        handles.append(handle)
                        button['action'] = {'type': 1, 'data': handle, 'permission': {'type': 0, 'specify_user_ids': [user, *sorted(admins - {user})]}, 'unsupport_tips': str(present('callback.unsupported', plain=True))}
                segment.data['keyboard'] = MessageKeyboard.model_validate(keyboard)
            self.prune()
            return (prepared, handles)
        except BaseException:
            for handle in handles:
                self.actions.pop(handle, None)
            raise

    def trusted(self, bot, event):
        if (not isinstance(event, InteractionCreateEvent) or event.type != 11
                or not event.application_id == str(bot.self_id) == self.config.app_id
                or event.guild_id or event.channel_id):
            return False
        if event.chat_type == 2 and event.scene == 'c2c':
            return (not event.group_openid and not event.group_member_openid
                    and isinstance(event.user_openid, str) and bool(event.user_openid)
                    and self.config.allows('c2c', event.user_openid))
        if event.chat_type == 1 and event.scene == 'group':
            return (not event.user_openid and isinstance(event.group_openid, str)
                    and isinstance(event.group_member_openid, str) and bool(event.group_member_openid)
                    and self.config.allows('group_at', event.group_member_openid, event.group_openid))
        return False

    async def ack(self, bot, event, code, timeout=ACK_SECONDS):
        try:
            await asyncio.wait_for(bot.put_interaction(interaction_id=event.id, code=code), timeout=timeout)
            report('callback_ack_accepted' if code == 0 else 'callback_rejected')
            return True
        except Exception as exc:
            report('callback_ack_failed', exc)
            return False

    def source(self, event, command):
        if event.chat_type == 1:
            return GroupAtMessageCreateEvent.model_validate({
                'id': 'callback:' + event.id, 'content': command, 'timestamp': event.timestamp,
                'group_id': event.group_openid, 'group_openid': event.group_openid,
                # 点击事件不提供群角色，按普通成员构造，不从按钮继承管理权限。
                'author': {'id': event.group_member_openid, 'member_openid': event.group_member_openid,
                           'bot': False, 'member_role': 'member'}})
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
        resolved = event.data.resolved
        action = self.actions.get(resolved.button_data)
        actor = event.get_user_id()
        valid = (action is not None and action.ready and action.app == event.application_id
                 and (action.user == actor or (action.group and UNDO_COMMAND.fullmatch(action.command)
                                               and actor in action.admins and actor in self.config.admins))
                 and action.group == (event.group_openid or '')
                 and action.button_id == resolved.button_id
                 and (not resolved.message_id or resolved.message_id == action.message_id))
        if not -1 <= age < 2.8:
            report('callback_event_expired')
            if valid and not action.claimed and UNDO_COMMAND.fullmatch(action.command):
                await self.hint(bot, event, present('callback.callbacks_1', {}))
            return
        if not valid or action.claimed:
            await self.ack(bot, event, 3 if valid else 1)
            # 无效、过期或已使用按钮只确认拒绝，不在聊天中重复提醒。
            return
        if self.inflight >= 2:
            await self.ack(bot, event, 2)
            return
        action.claimed = True
        self.inflight += 1
        if not await self.ack(bot, event, 0, timeout=min(ACK_SECONDS, 2.8 - max(0, age))):
            self.inflight -= 1
            if UNDO_COMMAND.fullmatch(action.command):
                await self.hint(bot, event, present('callback.callbacks_3', {}))
            return
        task = asyncio.create_task(self.execute(bot, event, action.command))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def execute(self, bot, event, command):
        confirmed = CARD_NAME_CONFIRMED.set(True)
        try:
            source = self.source(event, command)
            proxy = CallbackBot(self, bot, source, interaction=event)
            undo = UNDO_COMMAND.fullmatch(command)
            if undo:
                if self.undo_handler is None:
                    raise ValueError('Alias undo handler unavailable')
                await asyncio.wait_for(self.undo_handler(proxy, source, undo[1]), timeout=BUSINESS_SECONDS)
            else:
                await asyncio.wait_for(self.processor(proxy, source), timeout=BUSINESS_SECONDS)
        except Exception as exc:
            report('callback_business_failed', exc)
        finally:
            CARD_NAME_CONFIRMED.reset(confirmed)
            self.inflight -= 1
