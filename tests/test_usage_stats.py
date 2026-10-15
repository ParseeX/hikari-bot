"""只用临时库和伪造事件验证统计，不连接 QQ 或读取生产凭据。"""
import asyncio
from contextlib import closing
import importlib
import sqlite3
from types import SimpleNamespace

from hikari_bot.services.usage_stats import UsageStats


def test_daily_aggregation_dedup_and_no_identifiers(tmp_path):
    store = UsageStats(tmp_path / 'usage.sqlite3')
    store.initialize(['卡价查询', '卡组查询'])
    assert store.record('卡价查询', 'group', 'private-message-id')
    assert not store.record('卡价查询', 'group', 'private-message-id')
    assert store.record('卡价查询', 'private', 'another-message-id')
    with closing(sqlite3.connect(store.path)) as db:
        assert dict(db.execute('SELECT scope,sum(count) FROM counts GROUP BY scope')) == {'group': 1, 'private': 1}
        start = db.execute("SELECT value FROM metadata WHERE key='started_at'").fetchone()[0]
        dump = '\n'.join(db.iterdump())
        assert 'message-id' not in dump
        assert [row[1] for row in db.execute('PRAGMA table_info(counts)')] == ['day', 'feature', 'scope', 'count']
    store.initialize(['卡价查询'])
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='started_at'").fetchone()[0] == start
        assert db.execute('SELECT sum(count) FROM counts').fetchone()[0] == 2


def test_unknown_feature_or_scope_is_rejected(tmp_path):
    import pytest
    store = UsageStats(tmp_path / 'usage.sqlite3')
    store.initialize(['卡价查询'])
    with pytest.raises(ValueError):
        store.record('unknown', 'group', 'one')
    with pytest.raises(ValueError):
        store.record('卡价查询', 'user-id', 'two')


def test_matcher_aliases_continuations_permissions_and_failure_isolation(tmp_path, monkeypatch):
    import nonebot
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(_env_file=None, command_start={''})
    from nonebot.adapters.onebot.v11 import PrivateMessageEvent, GroupMessageEvent
    from nonebot.matcher import Matcher
    from hikari_bot.core.commands import on_cmd
    plugin = importlib.import_module('hikari_bot.plugins.usage_stats')
    matcher_type = on_cmd('统计测试', aliases={'测试别名'})
    @matcher_type.handle()
    async def handle(event: PrivateMessageEvent):
        pass
    features = plugin.configure_matchers()
    assert '统计测试' in features and '测试别名' not in features
    store = UsageStats(tmp_path / 'usage.sqlite3')
    store.initialize(features)
    monkeypatch.setattr(plugin, 'store', store)
    def event(cls, kind, message='统计测试', mid=123):
        return cls.model_validate(dict(time=1, self_id=2, post_type='message', message_type=kind,
            sub_type='friend' if kind == 'private' else 'normal', message_id=mid, user_id=3,
            group_id=4, message=message, original_message=message, raw_message=message, font=0, sender={}))
    private = event(PrivateMessageEvent, 'private')
    group = event(GroupMessageEvent, 'group')
    assert not plugin.should_count(matcher_type(), group)
    bot = SimpleNamespace(self_id='2')
    first = matcher_type()
    asyncio.run(plugin.count_usage(bot, private, first))
    # 重复投递、同一 matcher 多次处理和临时续接均不增加计数。
    asyncio.run(plugin.count_usage(bot, private, matcher_type()))
    asyncio.run(plugin.count_usage(bot, private, first))
    continuation = Matcher.new('message', default_state=first.state, temp=True)
    asyncio.run(plugin.count_usage(bot, event(PrivateMessageEvent, 'private', '1', 124), continuation()))
    # 新的别名调用仍归入原功能。
    asyncio.run(plugin.count_usage(bot, event(PrivateMessageEvent, 'private', '测试别名', 125), matcher_type()))
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute('SELECT sum(count) FROM counts').fetchone()[0] == 2
    monkeypatch.setattr(store, 'record', lambda *args: (_ for _ in ()).throw(OSError('secret content')))
    asyncio.run(plugin.count_usage(bot, event(PrivateMessageEvent, 'private', mid=126), matcher_type()))
    matcher_type.destroy()
    continuation.destroy()


def test_real_dispatch_alias_and_multiturn_count_only_once(tmp_path, monkeypatch):
    import nonebot
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init(_env_file=None, command_start={''})
    from nonebot.adapters.onebot.v11 import Adapter, Bot, PrivateMessageEvent
    from nonebot.message import handle_event
    from nonebot.matcher import matchers
    from nonebot.params import ArgPlainText
    from hikari_bot.core.commands import on_cmd
    plugin = importlib.import_module('hikari_bot.plugins.usage_stats')
    sent = []
    async def send(*args, **kwargs):
        sent.append(str(args[1] if len(args) > 1 else kwargs.get('message')))
        return {'message_id': len(sent)}
    bot = Bot(Adapter(nonebot.get_driver()), '2')
    monkeypatch.setattr(bot, 'send', send)
    test_matcher = on_cmd('计数集成测试', aliases={'计数别名'}, priority=1, block=True)
    @test_matcher.handle()
    async def first():
        await test_matcher.send('请选择编号')
    @test_matcher.got('choice')
    async def choice(value: str = ArgPlainText('choice')):
        if value != '1':
            await test_matcher.reject('请回复1')
        await test_matcher.send('第一条')
        await test_matcher.finish('第二条')
    denied = on_cmd('禁止计数测试', permission=lambda: False, priority=1)
    @denied.handle()
    async def never():
        raise AssertionError('permission failed')
    store = UsageStats(tmp_path / 'usage.sqlite3')
    store.initialize(plugin.configure_matchers())
    monkeypatch.setattr(plugin, 'store', store)
    async def dispatch():
        for mid, text in enumerate(['禁止计数测试', '计数别名', '0', '1'], 101):
            event = PrivateMessageEvent.model_validate(dict(time=1, self_id=2, post_type='message',
                message_type='private', sub_type='friend', message_id=mid, user_id=3,
                message=text, original_message=text, raw_message=text, font=0, sender={}))
            await handle_event(bot, event)
    try:
        asyncio.run(dispatch())
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute('SELECT feature,scope,count FROM counts').fetchall() == [('计数集成测试', 'private', 1)]
        assert len(sent) >= 3
    finally:
        test_matcher.destroy()
        denied.destroy()
