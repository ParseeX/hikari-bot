"""
commands.py — on_command 的封装，强制要求命令与参数之间必须有空格（或无参数）。

使用方式与 on_command 完全相同，直接替换导入即可：
    from hikari_bot.core.commands import on_cmd
    handler = on_cmd("卡价查询", aliases={"查卡价"}, priority=5)
"""

from nonebot import get_driver, on_command
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import MessageEvent


def _space_sep(*cmds: str) -> Rule:
    """生成 Rule：消息文本必须恰好等于命令名，或以「命令名+空格」开头。"""
    starts = get_driver().config.command_start
    patterns = frozenset(s + c for s in starts for c in cmds)

    def _check(event: MessageEvent) -> bool:
        t = event.get_plaintext()
        return any(t == p or t.startswith(p + " ") for p in patterns)

    return Rule(_check)


def on_cmd(cmd: str, **kwargs):
    """
    带「空格分隔」限制的 on_command 封装。
    自动从 cmd 和 aliases 提取全部命令名并生成对应 Rule，
    若调用者额外传入 rule=，会与该 Rule 做 AND 合并。
    """
    aliases = kwargs.pop("aliases", set())
    all_names = {cmd} | (aliases if isinstance(aliases, set) else set(aliases))
    space_rule = _space_sep(*all_names)
    if "rule" in kwargs:
        space_rule = space_rule & kwargs.pop("rule")
    return on_command(cmd, aliases=aliases, rule=space_rule, **kwargs)
