"""独立管理员身份与私聊边界，不继承数字 QQ 或群管理员权限。"""
import httpx

from hikari_bot.services.platform import Identity, Result


class AdminQueries:
    def __init__(self, config, *, power=None):
        self.admins = {Identity('qq_official', config.app_id, 'user', value).key for value in config.admins}
        self.power = power

    async def handle(self, request, command, argument):
        if command not in {'开机', '关机'}:
            return None
        if request.conversation.kind != 'user' or request.actor.key not in self.admins:
            return Result('权限不足：NAS 控制仅限单独核验的官方管理员私聊使用。')
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
