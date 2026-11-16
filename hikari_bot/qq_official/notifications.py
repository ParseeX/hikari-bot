"""官方主动消息投递与显式订阅，接口成功与客户端验收分开记录。"""
from .output_templates import present
import asyncio
import json
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.exception import ActionFailed

from hikari_bot.services.platform import Identity, Result
from hikari_bot.services.official_bus import decode, encode
from .media import NamedFileBot, output_message


class Notifications:
    def __init__(self, bus, state, config, *, enabled=False, daily_limit=20):
        self.bus, self.state, self.config = bus, state, config
        self.enabled, self.daily_limit = enabled, daily_limit
        self.bot, self.task = None, None

    async def handle(self, request, command, argument):
        if command not in {'开启通知', '关闭通知', '通知状态', '测试通知'}:
            return None
        if request.conversation.kind != 'user' or argument:
            return Result(present('notifications.notifications_dccaa328', {}))
        target = request.actor
        if command == '通知状态':
            online, counts = await asyncio.to_thread(self.bus.status)
            topics = await asyncio.to_thread(self.bus.topics, target)
            return Result(present('notifications.notifications_8aa32ad1', {'value1': '开' if self.enabled else '暂停，等待平台验证', 'value2': '在线' if online else '离线', 'value3': '、'.join(sorted(topics - {'test'})) or '关闭', 'counts': counts}))
        if command == '测试通知':
            await asyncio.to_thread(self.bus.allow_topic, target, 'test', True)
            key = await asyncio.to_thread(self.bus.enqueue, 'notice', target.key, 'test',
                encode(Result(present('notifications.notifications_badbb88c', {}))),
                request.event_id, lifetime=60)
            for _ in range(8):
                await asyncio.sleep(1)
                status, result = await asyncio.to_thread(self.bus.response, key)
                if status not in {'pending', 'processing'}:
                    prefix, suffix = present('notifications.notifications_72f0156e', {}), present('notifications.notifications_f88d4bc1', {})
                    detail = '\n' + result.text if result else ''
                    return Result(present('notifications.notifications_body_83c09af2', {'content': prefix + status + detail + suffix,
                        'prefix': prefix, 'status': status, 'detail': detail, 'suffix': suffix}))
            return Result(present('notifications.notifications_3e362d30', {}))
        enabled = command == '开启通知'
        await asyncio.to_thread(self.bus.allow_topic, target, 'mycard', enabled)
        if target.opaque_id in self.config.admins:
            await asyncio.to_thread(self.bus.allow_topic, target, 'admin', enabled)
        if enabled and self.enabled:
            return Result(present('notifications.notifications_41c5bd83', {}))
        return Result(present('notifications.notifications_c712e9a8', {}) if enabled else present('notifications.notifications_a5f2b484', {}))

    async def tick(self):
        if self.bot is None:
            return
        job = await asyncio.to_thread(self.bus.claim, 'notice', app=self.config.app_id,
                                      daily_limit=self.daily_limit, topic=None if self.enabled else 'test')
        if job is None:
            return
        try:
            platform, app, kind, scope, opaque = json.loads(job['target'])
            if platform != 'qq_official' or app != self.config.app_id or kind not in {'user', 'group'} or scope:
                raise ValueError('invalid target')
            player = json.loads(job['body']).get('subscription')
            if player is not None:
                rows = await asyncio.to_thread(self.state.subscriptions, Identity(platform, app, kind, opaque, scope))
                if player not in {name for name, _ in rows}:
                    await asyncio.to_thread(self.bus.finish, job['id'], 'cancelled')
                    return
            result = decode(job['body'])
            message = output_message(result.text)
            parts = []
            if getattr(result.text, 'mode', 'text') == 'markdown' and (result.images or result.file):
                # Markdown 与上传媒体使用不同 QQ 消息类型，明确拆分。
                parts.append(message)
                message = Message()
            for image in result.images:
                message += MessageSegment.file_image(image)
            if result.file:
                message += MessageSegment.file_file(result.file, file_name=result.filename)
            parts.append(message)
            bot = NamedFileBot(self.bot)
            for part in parts:
                if kind == 'user':
                    await asyncio.wait_for(bot.send_to_c2c(openid=opaque, message=part), 90)
                else:
                    await asyncio.wait_for(bot.send_to_group(group_openid=opaque, message=part), 90)
            await asyncio.to_thread(self.bus.finish, job['id'], 'api_accepted')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None) if isinstance(error, ActionFailed) else None
            if isinstance(error, ActionFailed) and (error.status_code in {401,403,429} or code == 22009):
                await asyncio.to_thread(self.bus.set_setting, job['target'], 'pause_until', int(self.bus.clock() + 3600))
            detail = type(error).__name__ + (f' code={code}' if isinstance(code, int) else '')
            prefix, suffix = present('notifications.notifications_6a23ccee', {}), present('notifications.notifications_66cd9456', {})
            await asyncio.to_thread(self.bus.finish, job['id'], 'failed', Result(present('notifications.notifications_body_e4127616',
                {'content': prefix + detail + suffix, 'prefix': prefix, 'error': detail, 'suffix': suffix})))

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # 队列不可用时不发送，也不记录消息正文/目标。
            await asyncio.sleep(2)

    async def connect(self, bot):
        if str(bot.self_id) != self.config.app_id:
            return
        self.bot = bot
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def disconnect(self, bot):
        if self.bot is bot:
            self.bot = None

    async def close(self):
        self.bot = None
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
