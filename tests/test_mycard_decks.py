import asyncio
from collections import Counter
import importlib.util
import json
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot.permission import SUPERUSER

from hikari_bot.services.mycard_archive.library import DeckLibrary
from hikari_bot.services.mycard_archive.presentation import deck_links
from hikari_bot.services.mycard_archive.queries import Leaderboard, player_text, top_texts
from hikari_bot.services.mycard_archive.store import ArchiveStore
from test_mycard_archive import replay, decode_ourygo


def record(store, room, ts, main=(1, 1, 2), extra=(10,), side=()):
    mid, _ = store.begin(room, ['甲', '乙'])
    store.save_replay(mid, replay(main_a=main, extra=extra, timestamp=ts))
    if side:
        store.save_replay(mid, replay(main_a=main + side, extra=extra, timestamp=ts + 1))
    store.finalize(mid, True)
    return mid


def test_matching_core_merges_side_counts_without_losing_snapshots(tmp_path):
    store = ArchiveStore(tmp_path, {'1'})
    record(store, 'one', 100, side=(3, 3, 4))
    record(store, 'two', 200, main=(2, 1, 1), side=(3, 3, 3, 5))
    current = store.library.latest('甲')
    assert current.deck.main == (1, 1, 2)
    assert current.deck.side == (3, 3, 3, 4, 5)
    assert current.observations == 2 and current.updated_at >= current.first_seen
    assert len(store.library.history('甲')) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM deck_observations WHERE player='甲'").fetchone()[0] == 2
    assert store.library.latest('unknown') is None


@pytest.mark.parametrize('main,extra', [((1, 2), (10,)), ((1, 1, 2), (11,)), ((7,), (12,))])
def test_any_core_change_starts_fresh_side_and_old_versions_are_kept(tmp_path, main, extra):
    store = ArchiveStore(tmp_path, set())
    record(store, 'old', 100, side=(3,))
    record(store, 'new', 200, main=main, extra=extra, side=(4,))
    record(store, 'return', 300)
    history = store.library.history('甲')
    assert [v.deck.side for v in history] == [(3,), (4,), ()]
    assert [v.first_seen for v in history] == [100, 200, 300]


def test_late_older_match_does_not_replace_latest_core(tmp_path):
    store = ArchiveStore(tmp_path, set())
    record(store, 'new', 300, main=(99,), side=(4,))
    record(store, 'old', 100, side=(3,))
    assert store.library.latest('甲').deck.main == (99,)
    assert store.library.latest('甲').deck.side == (4,)


def test_backfill_and_restart_preserve_existing_archive_and_deliveries(tmp_path):
    store = ArchiveStore(tmp_path, {'1'})
    mid = record(store, 'old', 100, side=(3,))
    before = (tmp_path / mid / 'manifest.json').read_bytes()
    messages = store.pending()
    with store.connect() as db:
        db.execute('DROP TABLE deck_observations')
        db.execute('DROP TABLE deck_library_index')
    store = ArchiveStore(tmp_path, {'1'})
    store.backfill_library()
    assert store.library.latest('甲').deck.side == (3,)
    assert store.library.latest('甲').observations == 1
    assert store.pending() == messages
    assert (tmp_path / mid / 'manifest.json').read_bytes() == before
    # 查询自身不会写入；没有启用采集 Runtime 也能使用。
    assert DeckLibrary(tmp_path / 'archive.sqlite3').latest('甲') == store.library.latest('甲')


def test_partial_replay_is_saved_but_empty_failure_does_not_reset_deck(tmp_path):
    store = ArchiveStore(tmp_path, set())
    mid, _ = store.begin('partial', ['甲', '乙'])
    store.save_replay(mid, replay(timestamp=100))
    store.finalize(mid, False)
    known = store.library.latest('甲')
    failed, _ = store.begin('failed', ['甲', '乙'])
    store.finalize(failed, False)
    assert store.library.latest('甲') == known


def test_link_contains_merged_side_and_large_side_is_not_truncated(tmp_path):
    store = ArchiveStore(tmp_path, set())
    record(store, 'one', 100, side=tuple(range(100, 115)))
    record(store, 'two', 200, side=(200, 201))
    version = store.library.latest('甲')
    text = player_text('甲', version)
    assert '更新：' in text and 'JST' not in text
    assert text.splitlines()[1:] == ['https://ygo.xyk.one/mc/%E7%94%B2']
    links = deck_links(version.deck)
    assert len(links) == 2
    seen = Counter()
    for link in links:
        main, extra, side = decode_ourygo(link)
        assert main == Counter(version.deck.main) and extra == Counter(version.deck.extra)
        seen += side
    assert seen == Counter(version.deck.side)


def test_query_link_encodes_username_as_path(tmp_path):
    store = ArchiveStore(tmp_path, set())
    record(store, 'one', 100)
    player = '中文 空格/#?%'
    link = player_text(player, store.library.latest('甲')).splitlines()[1]
    url = urlsplit(link)
    assert url.netloc == 'ygo.xyk.one' and not url.query and not url.fragment
    assert unquote(url.path.removeprefix('/mc/')) == player


def test_top100_preserves_live_rank_and_missing_players(tmp_path):
    async def run():
        store = ArchiveStore(tmp_path, set())
        record(store, 'one', 100)
        names = ['未知', '甲'] + [str(i) for i in range(98)]
        calls = []
        async def fetch():
            calls.append(1)
            return [{'username': name} for name in names]
        board = Leaderboard(fetch=fetch)
        texts = await top_texts(store.library, board)
        assert len(texts) == 100
        assert texts[0] == '1. 未知：暂无卡组'
        assert texts[1].startswith('2. 甲（更新：')
        assert texts[1].splitlines()[1:] == ['https://ygo.xyk.one/mc/%E7%94%B2']
        assert texts[-1] == '100. 97：暂无卡组'
        await board.players()
        assert len(calls) == 1
        board.expires = 0
        await board.players()
        assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize('rows', [[{'username': 'one'}], [{'username': 'same'}] * 100, {}])
def test_invalid_leaderboard_is_not_presented_as_top100(rows):
    async def fetch():
        return rows
    with pytest.raises(ValueError):
        asyncio.run(Leaderboard(fetch=fetch).players())


class Finished(Exception):
    pass


def load_plugin(monkeypatch):
    class Matcher:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.message = None
        def handle(self):
            return lambda fn: fn
        async def finish(self, message):
            self.message = message
            raise Finished()
    import hikari_bot.core.commands
    monkeypatch.setattr(hikari_bot.core.commands, 'on_cmd', lambda cmd, **kw: Matcher(**kw))
    spec = importlib.util.spec_from_file_location('deck_query_test', Path('hikari_bot/plugins/mycard_decks.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('group', [False, True])
def test_command_permissions_and_forward_destination(monkeypatch, tmp_path, group):
    module = load_plugin(monkeypatch)
    assert module.deck_query.options['permission'] is SUPERUSER
    assert 'permission' not in module.top_decks.options
    store = ArchiveStore(tmp_path, set())
    record(store, 'one', 100)
    monkeypatch.setattr(module, 'library', lambda: store.library)
    event = GroupMessageEvent.model_construct(group_id=222, user_id=111) if group else SimpleNamespace(user_id=111)
    with pytest.raises(Finished):
        asyncio.run(module.query_player(event, Message('甲')))
    assert 'https://ygo.xyk.one/mc/%E7%94%B2' in str(module.deck_query.message)
    async def fetch():
        return [{'username': '甲'}] + [{'username': str(i)} for i in range(99)]
    module.leaderboard = Leaderboard(fetch=fetch)
    calls = []
    async def call(api, **kwargs):
        calls.append((api, kwargs))
    bot = SimpleNamespace(self_id='333', call_api=call)
    asyncio.run(module.query_top(bot, event))
    assert len(calls) == 1
    api, data = calls[0]
    assert api == ('send_group_forward_msg' if group else 'send_private_forward_msg')
    assert data['group_id' if group else 'user_id'] == (222 if group else 111)
    assert len(data['messages']) == 10
    assert all(node.data['content'][0].type == 'text' for node in data['messages'])


@pytest.mark.parametrize('group', [False, True])
def test_local_history_must_not_hide_unconfirmed_forward(monkeypatch, group):
    module = load_plugin(monkeypatch)
    async def texts(*args):
        return ['卡组'] * 100
    monkeypatch.setattr(module, 'top_texts', texts)
    calls = []
    async def call(api, **kwargs):
        calls.append(api)
        if api.startswith('send_'):
            raise ActionFailed(retcode=1200, message='发送转发消息（res_id：test/resource+abc 失败')
        assert api == ('get_group_msg_history' if group else 'get_friend_msg_history')
        return {'messages': [{'sender': {'user_id': 333}, 'real_seq': '1034389', 'time': time.time(),
                              'message': [{'type': 'json', 'data': {'data': json.dumps({
                                  'app': 'com.tencent.multimsg',
                                  'meta': {'detail': {'resid': 'test/resource+abc'}}})}}]}]}
    bot = SimpleNamespace(self_id='333', call_api=call)
    event = GroupMessageEvent.model_construct(group_id=222, user_id=111) if group else SimpleNamespace(user_id=111)
    with pytest.raises(Finished):
        asyncio.run(module.query_top(bot, event))
    assert module.top_decks.message == 'QQ 未确认发送结果，请先查看聊天记录'
    assert calls == (['send_group_forward_msg'] if group else ['send_private_forward_msg'])


@pytest.mark.parametrize('history_error', [False, True])
def test_unconfirmed_forward_is_not_retried_or_claimed_successful(monkeypatch, history_error):
    module = load_plugin(monkeypatch)
    async def texts(*args):
        return ['卡组'] * 100
    monkeypatch.setattr(module, 'top_texts', texts)
    calls = []
    async def call(api, **kwargs):
        calls.append(api)
        if api.startswith('send_'):
            raise ActionFailed(retcode=1200, message='发送转发消息（res_id：unconfirmed 失败')
        if history_error:
            raise TimeoutError()
        return {'messages': []}
    with pytest.raises(Finished):
        asyncio.run(module.query_top(SimpleNamespace(self_id='333', call_api=call), SimpleNamespace(user_id=111)))
    assert module.top_decks.message == 'QQ 未确认发送结果，请先查看聊天记录'
    assert calls == ['send_private_forward_msg']


def test_real_forward_rejection_is_still_reported_without_history_probe(monkeypatch):
    module = load_plugin(monkeypatch)
    async def texts(*args):
        return ['卡组'] * 100
    monkeypatch.setattr(module, 'top_texts', texts)
    calls = []
    async def call(api, **kwargs):
        calls.append(api)
        raise ActionFailed(retcode=1400, message='invalid payload')
    with pytest.raises(Finished):
        asyncio.run(module.query_top(SimpleNamespace(self_id='333', call_api=call), SimpleNamespace(user_id=111)))
    assert module.top_decks.message == '卡组消息发送失败（ActionFailed，retcode=1400）'
    assert calls == ['send_private_forward_msg']


def test_forward_preview_is_small_while_all_100_decks_remain_inside(monkeypatch):
    module = load_plugin(monkeypatch)
    texts = [f'{i}. 玩家{i}\nhttp://deck.ourygo.top/?d=' + 'x' * 350 for i in range(1, 101)]
    async def full_board(*args):
        return texts
    monkeypatch.setattr(module, 'top_texts', full_board)
    async def call(api, **kwargs):
        assert api == 'send_group_forward_msg'
        body = '\n\n'.join(str(node.data['content']) for node in kwargs['messages'])
        assert all(text in body for text in texts)
        assert len(kwargs['messages']) == 10
        # NapCat 没有 news 时会复制所有节点的全文到外层 ARK；覆盖这一真实默认行为。
        news = kwargs.get('news') or [{'text': str(node.data['content'])} for node in kwargs['messages']]
        assert len(json.dumps(news, ensure_ascii=False).encode()) < 1024
    bot = SimpleNamespace(self_id='333', call_api=call)
    asyncio.run(module.query_top(bot, GroupMessageEvent.model_construct(group_id=222, user_id=111)))
