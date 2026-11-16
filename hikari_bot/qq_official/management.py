"""官方平台内的管理操作，不把 OpenID 当成旧 QQ 号或群号。"""
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
            return Result('权限不足：此操作仅允许已单独核验的官方管理员私聊执行。')
        if command in {'开启解析','关闭解析'}:
            # 不假装更改旧群开关；管理当前官方私聊的解析。
            if argument:
                return Result('该命令只管理当前官方会话，请不要填写旧 QQ 群号。')
            await asyncio.to_thread(self.bus.set_setting, request.actor.key, 'parser', command == '开启解析')
            return Result(f'当前官方私聊的链接解析已{"开启" if command == "开启解析" else "关闭"}。旧群设置未改变。')
        if command in {'白名单','添加至白名单','清空白名单'}:
            if command == '白名单' and not argument:
                return Result(f'官方允许私聊：{len(self.config.users)}；已核验群：{len(self.config.groups)}。\n'
                              '群关联需使用接收专用配对流程核验 OpenID；旧群号不能直接填入。')
            return Result('尚未实施官方白名单增删入口；需先核验对应群的 OpenID。未改原白名单，也未清空任何记录。')
        if command in {'切换AI权限','切换本地模型权限'}:
            return Result('原本地模型聊天模块当前未启用。尚未迁移官方群身份授权，未改变原权限开关。')
        if command == '重置卡价数据库':
            return Result('重置会删除共享卡价历史；官方入口保留此提示，不执行删除。请通过受控维护流程备份后操作。')
        if command == '广播':
            if not argument:
                return Result('用法：广播 内容')
            targets = [key for key in await asyncio.to_thread(self.bus.recipients, 'broadcast')
                       if json.loads(key)[2] == 'group']
            if not targets:
                return Result('当前没有完成群身份核验并开启主动通知的群；未广播。')
            await asyncio.to_thread(self.bus.publish, Result(argument), dedupe=request.event_id, topic='broadcast', targets=targets)
            return Result(f'已为{len(targets)}个已核验群排队；这不代表客户端已收到。')
        if command == '成员列表':
            groups = sorted(self.config.groups)
            if not groups:
                return Result('当前没有已核验的官方群，尚不能查询成员。')
            if not argument.isdigit() or not 1 <= int(argument) <= len(groups):
                return Result(f'用法：成员列表 群序号（1—{len(groups)}）。返回官方群成员 OpenID，不返回原数字 QQ 号。')
            if self.bot is None or not hasattr(self.bot, 'get_group_members'):
                return Result('当前适配器或连接不支持群成员查询。')
            rows, cursor, seen = [], None, set()
            for _ in range(10):
                page = await self.bot.get_group_members(group_openid=groups[int(argument)-1], cursor=cursor)
                for member in page.members:
                    rows.append(f'{member.username or "未提供昵称"}，{member.member_role or "未知角色"}，{member.member_openid}')
                cursor = page.next_cursor
                if not cursor or cursor in seen:
                    break
                seen.add(cursor)
            if cursor:
                rows.append('已达到本次10页查询上限，列表未全部取完。')
            return Result('\n'.join(rows) or '平台未返回群成员；需要核对群管理权限。')
        if command == '确认操作':
            item = self.confirmations.pop(request.actor.key, None)
            if not item or item[1] != argument or item[2] < time.monotonic():
                return Result('确认码无效或已过期，未执行操作。')
            action = self.restart if item[0] == 'restart' else self.reboot
            if action is None:
                return Result('维护操作尚未配置。')
            asyncio.create_task(self.delayed(action))
            return Result('已确认，3秒后执行。官方会话将短暂断开。')
        operation = 'restart' if command in {'重载','重载插件','reload'} else 'reboot'
        if argument:
            return Result('该维护命令不接受参数。')
        if (self.restart if operation == 'restart' else self.reboot) is None:
            return Result('该维护操作尚未配置；没有执行重启。')
        code = secrets.token_hex(3)
        self.confirmations[request.actor.key] = (operation, code, time.monotonic()+60)
        while len(self.confirmations)>16:
            self.confirmations.popitem(last=False)
        detail = '重新启动当前官方发布版' if operation == 'restart' else '重启整台服务器，会中断全部服务'
        return Result(f'{detail}。60秒内回复“确认操作 {code}”执行。不会拉取或覆盖代码。')

    @staticmethod
    async def delayed(action):
        await asyncio.sleep(3)
        await action()
