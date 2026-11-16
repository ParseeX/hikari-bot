"""官方主动消息投递与显式订阅，接口成功与客户端验收分开记录。"""
import asyncio
import json
from nonebot.adapters.qq import Message, MessageSegment
from nonebot.adapters.qq.exception import ActionFailed

from hikari_bot.services.platform import Identity, Result
from hikari_bot.services.official_bus import decode, encode
from .media import NamedFileBot


class Notifications:
    def __init__(self, bus, state, config, *, enabled=False, daily_limit=20):
        self.bus, self.state, self.config = bus, state, config
        self.enabled, self.daily_limit = enabled, daily_limit
        self.bot, self.task = None, None

    async def handle(self, request, command, argument):
        if command not in {'开启通知', '关闭通知', '通知状态', '测试通知'}:
            return None
        if request.conversation.kind != 'user' or argument:
            return Result('请在机器人私聊中发送该命令，不带参数。群主动推送需要单独核验。')
        target = request.actor
        if command == '通知状态':
            online, counts = await asyncio.to_thread(self.bus.status)
            topics = await asyncio.to_thread(self.bus.topics, target)
            return Result(f'主动投递总开关：{"开" if self.enabled else "暂停，等待平台验证"}\n采集后台：{"在线" if online else "离线"}\n'
                          f'本会话通知：{"、".join(sorted(topics - {"test"})) or "关闭"}\n队列状态：{counts}\napi_accepted 只表示接口接受，不代表客户端已验收。')
        if command == '测试通知':
            await asyncio.to_thread(self.bus.allow_topic, target, 'test', True)
            key = await asyncio.to_thread(self.bus.enqueue, 'notice', target.key, 'test',
                encode(Result('官方主动通知测试：此条未引用你的消息 ID。请确认客户端实际可见。')),
                request.event_id, lifetime=60)
            for _ in range(8):
                await asyncio.sleep(1)
                status, result = await asyncio.to_thread(self.bus.response, key)
                if status not in {'pending', 'processing'}:
                    return Result('主动通知测试结果：' + status + ('\n' + result.text if result else '') + '\n仍需以客户端看到测试消息为准。')
            return Result('测试已排队，请稍后查看通知状态。')
        enabled = command == '开启通知'
        await asyncio.to_thread(self.bus.allow_topic, target, 'mycard', enabled)
        if target.opaque_id in self.config.admins:
            await asyncio.to_thread(self.bus.allow_topic, target, 'admin', enabled)
        if enabled and self.enabled:
            return Result('已开启本会话通知；原私聊订阅通过已核验身份关联读取，群订阅不迁移。投递仍受官方权限及频控限制。')
        return Result('已保存接收意愿，但主动投递总开关仍暂停，需先完成平台验证。' if enabled else '已关闭本会话通知，未删除旧订阅。')

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
            message = Message(result.text)
            for image in result.images:
                message += MessageSegment.file_image(image)
            if result.file:
                message += MessageSegment.file_file(result.file, file_name=result.filename)
            bot = NamedFileBot(self.bot)
            if kind == 'user':
                await asyncio.wait_for(bot.send_to_c2c(openid=opaque, message=message), 90)
            else:
                await asyncio.wait_for(bot.send_to_group(group_openid=opaque, message=message), 90)
            await asyncio.to_thread(self.bus.finish, job['id'], 'api_accepted')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, 'code', None) if isinstance(error, ActionFailed) else None
            if isinstance(error, ActionFailed) and (error.status_code in {401,403,429} or code == 22009):
                await asyncio.to_thread(self.bus.set_setting, job['target'], 'pause_until', int(self.bus.clock() + 3600))
            detail = type(error).__name__ + (f' code={code}' if isinstance(code, int) else '')
            await asyncio.to_thread(self.bus.finish, job['id'], 'failed', Result('投递失败：' + detail + '；未自动重试。'))

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
