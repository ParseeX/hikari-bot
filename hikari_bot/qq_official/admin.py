"""独立管理员身份与私聊边界，不继承数字 QQ 或群管理员权限。"""
import httpx
import asyncio
import json
from hikari_bot.services.official_control import CONTROL_COMMANDS

from hikari_bot.services.platform import Identity, Result


class AdminQueries:
    def __init__(self, config, *, power=None, bus=None, management=None):
        self.admins = {Identity('qq_official', config.app_id, 'user', value).key for value in config.admins}
        self.power = power
        self.bus = bus
        self.management = management

    async def handle(self, request, command, argument):
        if self.management:
            result = await self.management.handle(request, command, argument)
            if result is not None:
                return result
        if command not in {'开机', '关机'} and command not in CONTROL_COMMANDS:
            return None
        if request.conversation.kind != 'user' or request.actor.key not in self.admins:
            return Result('权限不足：管理命令仅限单独核验的官方管理员私聊使用。')
        if command in CONTROL_COMMANDS:
            if self.bus is None or not (await asyncio.to_thread(self.bus.status))[0]:
                return Result('管理后台当前离线，未执行操作。')
            body = json.dumps({'action': CONTROL_COMMANDS[command], 'argument': argument})
            key = await asyncio.to_thread(self.bus.enqueue, 'control', request.actor.key, 'control', body, request.event_id, lifetime=120)
            for _ in range(110):
                status, result = await asyncio.to_thread(self.bus.response, key)
                if result:
                    return result
                if status not in {'pending', 'processing'}:
                    return Result('后台操作未完成（' + status + '），未自动重试。')
                await asyncio.sleep(1)
            return Result('后台尚未确认完成，请查询状态；不要重复提交写入命令。')
        if argument:
            return Result('此命令不接受参数。')
        if self.power is None:
            return Result('NAS 电源控制尚未配置。')
        try:
            await self.power('wake' if command == '开机' else 'shutdown')
        except httpx.HTTPStatusError as error:
            return Result(f'NAS 未接受请求（HTTP {error.response.status_code}）。')
        except httpx.HTTPError:
            return Result('无法连接 NAS 电源服务；未自动重试。')
        except (RuntimeError, ValueError):
            return Result('NAS 电源服务返回异常；未自动重试。')
        return Result('已发送开机请求。' if command == '开机' else '已发送关机请求。')
