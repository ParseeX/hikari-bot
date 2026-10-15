"""在已匹配功能的入口计数，权限、业务处理与回复保持原样。"""
import asyncio
import inspect
from pathlib import Path
import re
import sys
from typing import get_type_hints

from nonebot import get_driver, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent
from nonebot.matcher import Matcher, matchers
from nonebot.message import run_preprocessor
from nonebot.rule import CommandRule

from hikari_bot.services.usage_stats import UsageStats


store = UsageStats(Path.home() / '.local/share/hikari-bot/usage.sqlite3')
SPECIAL = {
    ('mycard_query', 'mycard_query'): ('历史战绩查询', 'history'),
    ('ygomatch_query', 'collect_deck'): ('比赛卡组提交', 'deck_submission'),
}


def configure_matchers():
    for (module, name), (feature, gate) in SPECIAL.items():
        loaded = sys.modules.get('hikari_bot.plugins.' + module)
        matcher = getattr(loaded, name, None)
        if matcher:
            matcher._default_state.update(_usage_feature=feature, _usage_gate=gate)
    features = set()
    for group in list(matchers.values()):
        for matcher in list(group):
            if matcher.type != 'message' or matcher.temp:
                continue
            state = matcher._default_state
            if '_usage_feature' not in state:
                for checker in matcher.rule.checkers:
                    if isinstance(checker.call, CommandRule):
                        # on_cmd 已保留主命令；其他插件按注册命令归一，别名共享此 matcher。
                        state['_usage_feature'] = ' '.join(sorted(checker.call.cmds)[0])
                        break
            feature = state.get('_usage_feature')
            if feature:
                features.add(feature)
                # 不把仅限私聊/群聊的处理器在另一类消息上的空运行计为调用。
                accepted = []
                for handler in matcher.handlers:
                    try:
                        event_type = get_type_hints(handler.call).get('event')
                    except (NameError, TypeError):
                        event_type = None
                    if inspect.isclass(event_type) and issubclass(event_type, MessageEvent):
                        accepted.append(event_type)
                    else:
                        accepted.append(MessageEvent)
                state['_usage_event_types'] = tuple(accepted)
    return features


@get_driver().on_startup
async def start_usage_stats():
    features = configure_matchers()
    try:
        await asyncio.to_thread(store.initialize, features)
    except Exception as exc:
        logger.error('功能统计初始化失败：{}', type(exc).__name__)


def should_count(matcher, event):
    state = matcher.state
    if (not isinstance(event, (GroupMessageEvent, PrivateMessageEvent))
            or not state.get('_usage_feature') or state.get('_usage_counted')):
        return False
    event_types = state.get('_usage_event_types', ())
    if event_types and not isinstance(event, event_types):
        return False
    feature = state['_usage_feature']
    if feature in ('开机', '关机'):
        from hikari_bot.core.config import settings
        if not isinstance(event, PrivateMessageEvent) or str(event.user_id) not in settings.superusers:
            return False
    if state.get('_usage_gate') == 'history':
        module = sys.modules['hikari_bot.plugins.mycard_query']
        return bool(re.match(module.mycard_regex, event.get_plaintext().strip()))
    if state.get('_usage_gate') == 'deck_submission':
        module = sys.modules['hikari_bot.plugins.ygomatch_query']
        current = module.match_service.get_match_state()
        return bool(isinstance(event, PrivateMessageEvent) and current and
                    current['user_states'].get(str(event.user_id), {}).get('state') == 'waiting_for_deck')
    return True


@run_preprocessor
async def count_usage(bot: Bot, event: MessageEvent, matcher: Matcher):
    try:
        if not should_count(matcher, event):
            return
        # .got / .reject 的继续交互继承此状态；多条回复不产生额外计数。
        matcher.state['_usage_counted'] = True
        scope = 'group' if isinstance(event, GroupMessageEvent) else 'private'
        await asyncio.to_thread(store.record, matcher.state['_usage_feature'], scope,
                                (bot.self_id, event.time, event.message_id))
    except Exception as exc:
        # 统计失败不能阻断功能或把消息内容带入日志。
        logger.warning('功能统计写入失败：{}', type(exc).__name__)
