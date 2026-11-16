"""同一机器人内跨群/私聊共用绑定，权限和消息目标保持隔离。"""
import asyncio
import sqlite3

import pytest

from test_qq_migration import request
from hikari_bot.qq_official.business import Queries
from hikari_bot.services.platform import Identity
from hikari_bot.services.platform_state import PlatformState, BindingConflict


@pytest.mark.parametrize('first', ['c2c', 'group', 'group_at'])
def test_bind_once_and_update_everywhere(tmp_path, first):
    state = PlatformState(tmp_path)
    queries = Queries(None, state=state)
    scopes = [request(kind='c2c'), request(kind='group', group='a'), request(kind='group_at', group='b')]
    result = asyncio.run(queries.handle(request(kind=first), '绑定', '玩家甲'))
    assert result.text == '已成功绑定用户名 玩家甲。'
    for source in scopes:
        assert state.binding(source.actor) == '玩家甲'
        result = asyncio.run(queries.handle(source, '查询绑定', ''))
        assert '玩家甲' in result.text
    asyncio.run(queries.handle(scopes[1], '绑定', '玩家乙'))
    assert all(state.binding(source.actor) == '玩家乙' for source in scopes)
    assert state.binding(request(user='other').actor) is None
    assert state.binding(Identity('qq_official', 'other-app', 'user', 'u')) is None
    assert len({source.session for source in scopes}) == 3
    state.subscribe(scopes[0].actor, '订阅玩家')
    assert state.subscriptions(scopes[1].actor) == []
    assert state.legacy_subscriptions(scopes[1].actor) == []


def test_old_scoped_records_are_read_without_rebinding_or_rewriting(tmp_path):
    state = PlatformState(tmp_path)
    a, b, private = request('历史', kind='group', group='a'), request('历史', kind='group', group='b'), request('历史')
    with state.connect() as db:
        db.execute('INSERT INTO bindings VALUES (?,?)', (a.actor.key, '原用户名'))
    with sqlite3.connect(state.path) as db:
        before = db.execute('SELECT * FROM bindings').fetchall()
    assert state.binding(b.actor) == state.binding(private.actor) == '原用户名'
    with sqlite3.connect(state.path) as db:
        assert db.execute('SELECT * FROM bindings').fetchall() == before
        assert db.execute('SELECT * FROM shared_bindings').fetchall() == []


def test_conflicts_require_explicit_binding_and_preserve_old_rows(tmp_path):
    state = PlatformState(tmp_path)
    a, b, private = request('历史', kind='group', group='a'), request('历史', kind='group', group='b'), request('历史')
    with state.connect() as db:
        db.executemany('INSERT INTO bindings VALUES (?,?)', [(a.actor.key, '甲'), (b.actor.key, '乙')])
    for source in [a, b, private]:
        with pytest.raises(BindingConflict):
            state.binding(source.actor)
        result = asyncio.run(Queries(None, state=state).handle(source, '历史', ''))
        assert '绑定 用户名' in result.text and '不一致' in result.text
    state.bind(private.actor, '选定玩家')
    assert all(state.binding(source.actor) == '选定玩家' for source in [a,b,private])
    with sqlite3.connect(state.path) as db:
        assert db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0] == 2


def test_different_openids_are_not_merged_by_same_player_name(tmp_path):
    state = PlatformState(tmp_path)
    a, b = request(user='a').actor, request(user='b').actor
    state.bind(a, '同一用户名'); state.bind(b, '同一用户名')
    state.bind(a, '新用户名')
    assert state.binding(b) == '同一用户名'


def test_identical_openid_does_not_change_admin_identity(tmp_path):
    from hikari_bot.qq_official.config import PilotConfig
    from hikari_bot.qq_official.admin import AdminQueries
    from unittest.mock import AsyncMock
    state = PlatformState(tmp_path)
    user = request().actor
    member = request(kind='group').actor
    config = PilotConfig('app', 'fake', admins=frozenset({'u'}))
    state.bind(user, '玩家')
    assert state.binding(member) == '玩家'
    assert member.kind == 'member' and user.kind == 'user'
    assert member.key != user.key
    power = AsyncMock()
    admin = AdminQueries(config, power=power)
    result = asyncio.run(admin.handle(request(kind='group'), '关机', ''))
    assert result is not None
    power.assert_not_called()
