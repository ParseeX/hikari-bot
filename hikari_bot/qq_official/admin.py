"""独立管理员身份与私聊边界，不继承数字 QQ 或群管理员权限。"""
from .output_templates import present
from .diagnostics import exception_metadata
import logging
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
            return Result(present('operations.admin_768af6cc', {}))
        if command in CONTROL_COMMANDS:
            if self.bus is None or not (await asyncio.to_thread(self.bus.status))[0]:
                return Result(present('operations.admin_9a9d0430', {}))
            body = json.dumps({'action': CONTROL_COMMANDS[command], 'argument': argument})
            key = await asyncio.to_thread(self.bus.enqueue, 'control', request.actor.key, 'control', body, request.event_id, lifetime=120)
            for _ in range(110):
                status, result = await asyncio.to_thread(self.bus.response, key)
                if result:
                    return result
                if status not in {'pending', 'processing'}:
                    logging.getLogger(__name__).warning('Official control incomplete: %s', status)
                    prefix, suffix = present('operations.admin_992515ed', {}), present('operations.admin_b16086d4', {})
                    return Result(present('operations.admin_body_1a447932', {'content': prefix + status + suffix, 'prefix': prefix, 'status': status, 'suffix': suffix}))
                await asyncio.sleep(1)
            return Result(present('operations.admin_a7371c0b', {}))
        if argument:
            return Result(present('operations.admin_fc9b04d5', {}))
        if self.power is None:
            return Result(present('operations.admin_759478ab', {}))
        try:
            await self.power('wake' if command == '开机' else 'shutdown')
        except httpx.HTTPStatusError as error:
            logging.getLogger(__name__).warning('NAS power rejected: HTTP %d', error.response.status_code)
            return Result(present('operations.admin_6049a7a1', {'value1': error.response.status_code}))
        except httpx.HTTPError as error:
            logging.getLogger(__name__).warning('NAS power connection failed: %s', exception_metadata(error))
            return Result(present('operations.admin_de29891b', {}))
        except (RuntimeError, ValueError) as error:
            logging.getLogger(__name__).warning('NAS power invalid result: %s', exception_metadata(error))
            return Result(present('operations.admin_9e69e81d', {}))
        return Result(present('operations.admin_69f09f3b', {}) if command == '开机' else present('operations.admin_b1fd8b02', {}))
