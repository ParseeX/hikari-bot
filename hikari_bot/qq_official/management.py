"""官方平台内的管理操作，不把 OpenID 当成旧 QQ 号或群号。"""
from .output_templates import present
import asyncio
from collections import OrderedDict
import secrets
import time
import json
from hikari_bot.services.platform import Result


class Management:
    COMMANDS = {'白名单','添加至白名单','清空白名单','广播','成员列表','开启解析','关闭解析',
                '重载','重载插件','reload','重启服务器','reboot','确认操作','切换AI权限','切换本地模型权限',
                '重置卡价数据库'}

    def __init__(self, config, bus, *, restart=None, reboot=None):
        self.config, self.bus = config, bus
        self.restart, self.reboot = restart, reboot
        self.bot = None
        self.confirmations = OrderedDict()

    async def handle(self, request, command, argument):
        if command not in self.COMMANDS:
            return None
        if (request.actor.platform != 'qq_official' or request.actor.app != self.config.app_id
                or request.actor.kind != 'user' or request.actor.opaque_id not in self.config.admins
                or request.conversation != request.actor):
            return Result(present('management.management_d3590a67', {}))
        if command in {'开启解析','关闭解析'}:
            # 不假装更改旧群开关；管理当前官方私聊的解析。
            if argument:
                return Result(present('links.management_cb370695', {}))
            await asyncio.to_thread(self.bus.set_setting, request.actor.key, 'parser', command == '开启解析')
            return Result(present('links.management_23c1c8c8', {'value1': '开启' if command == '开启解析' else '关闭'}))
        if command in {'白名单','添加至白名单','清空白名单'}:
            return Result(present('whitelist.management_50fb5ef8', {}))
        if command in {'切换AI权限','切换本地模型权限'}:
            return Result(present('ai.management_88f6db5b', {}))
        if command == '重置卡价数据库':
            return Result(present('price_reset.management_56b8abba', {}))
        if command == '广播':
            if not argument:
                return Result(present('broadcast.management_ae2f939d', {}))
            targets = [key for key in await asyncio.to_thread(self.bus.recipients, 'broadcast')
                       if json.loads(key)[2] == 'group']
            if not targets:
                return Result(present('broadcast.management_d095c789', {}))
            await asyncio.to_thread(self.bus.publish, Result(present('broadcast.management_body_0f380231', {'content': argument})), dedupe=request.event_id, topic='broadcast', targets=targets)
            return Result(present('broadcast.management_bf9d7553', {'value1': len(targets)}))
        if command == '成员列表':
            groups = sorted(self.config.groups)
            if not groups:
                return Result(present('members.management_6a5e8eec', {}))
            if not argument.isdigit() or not 1 <= int(argument) <= len(groups):
                return Result(present('members.management_6a13324f', {'value1': len(groups)}))
            if self.bot is None or not hasattr(self.bot, 'get_group_members'):
                return Result(present('members.management_eb435656', {}))
            rows, cursor, seen = [], None, set()
            for _ in range(10):
                page = await self.bot.get_group_members(group_openid=groups[int(argument)-1], cursor=cursor)
                for member in page.members:
                    rows.append(present('members.row', {'name': member.username or '未提供昵称', 'role': member.member_role or '未知角色', 'openid': member.member_openid}))
                cursor = page.next_cursor
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
            if cursor:
                rows.append(present('members.page_limit'))
            return Result(present('members.management_body_b0942c38', {'content': '\n'.join(rows) or present('members.empty')}))
        if command == '确认操作':
            item = self.confirmations.pop(request.actor.key, None)
            if not item or item[1] != argument or item[2] < time.monotonic():
                return Result(present('maintenance.management_1c3ad00e', {}))
            action = self.restart if item[0] == 'restart' else self.reboot
            if action is None:
                return Result(present('maintenance.management_dec6ad65', {}))
            asyncio.create_task(self.delayed(action))
            return Result(present('maintenance.management_80bad791', {}))
        operation = 'restart' if command in {'重载','重载插件','reload'} else 'reboot'
        if argument:
            return Result(present('management.management_d87a1132', {}))
        if (self.restart if operation == 'restart' else self.reboot) is None:
            return Result(present('management.management_5e6b5000', {}))
        code = secrets.token_hex(3)
        self.confirmations[request.actor.key] = (operation, code, time.monotonic()+60)
        while len(self.confirmations)>16:
            self.confirmations.popitem(last=False)
        detail = present('management.management_e1b0483c', {}) if operation == 'restart' else present('management.management_7bb85b29', {})
        return Result(present('management.management_b3519626', {'detail': detail, 'code': code}))

    @staticmethod
    async def delayed(action):
        await asyncio.sleep(3)
        await action()
