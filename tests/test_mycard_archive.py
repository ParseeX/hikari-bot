import asyncio
import base64
import json
import lzma
import struct
import zipfile
from collections import Counter
from urllib.parse import parse_qs, urlparse

import pytest

from hikari_bot.services.mycard_archive.presentation import (
    build_messages, classify, consolidated_decks, deck_url, read_rules,
)
from hikari_bot.services.mycard_archive.replay import Deck, parse_replay
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime
from hikari_bot.services.mycard_archive.spectator import Credentials, CredentialError, join_packets, watch
from hikari_bot.services.mycard_archive.store import ArchiveStore
from hikari_bot.services.mycard_archive.ranking import RankFilter


def replay(main_a=(123, 123), main_b=(789,), extra=(456,), timestamp=100, *, compressed=True, extended=True):
    body = b''.join(name.encode('utf-16-le').ljust(40, b'\0') for name in ('甲', '乙'))
    body += struct.pack('<4I', 8000, 5, 1, 0)
    for main in (main_a, main_b):
        for cards in (main, extra):
            body += struct.pack('<I', len(cards)) + b''.join(struct.pack('<I', card) for card in cards)
    props, payload = bytes(8), body
    if compressed:
        packed = lzma.compress(body, format=lzma.FORMAT_ALONE)
        props, payload = packed[:5] + bytes(3), packed[13:]
    return (struct.pack('<6I8s', 0x32707279 if extended else 0x31707279,
                        0x1362, 0x10 | int(compressed), 0, len(body), timestamp, props)
            + (bytes(48) if extended else b'') + payload)


@pytest.mark.parametrize('compressed', [True, False])
@pytest.mark.parametrize('extended', [True, False])
def test_replay_formats(compressed, extended):
    decks = parse_replay(replay(compressed=compressed, extended=extended))
    assert decks == [Deck('甲', (123, 123), (456,)), Deck('乙', (789,), (456,))]
    assert 'Side INCOMPLETE' in decks[0].ydk()


@pytest.mark.parametrize('cut', [0, 15, 32, 79, -3])
def test_reject_truncated_replay(cut):
    with pytest.raises(ValueError):
        parse_replay(replay()[:cut])


def test_oversized_replay_and_path_traversal_are_rejected(tmp_path):
    raw = bytearray(replay())
    struct.pack_into('<I', raw, 16, 0xffffffff)
    with pytest.raises(ValueError):
        parse_replay(raw)
    store = ArchiveStore(tmp_path, {'1'})
    with pytest.raises(ValueError):
        store.bundle('../../credentials.json')


def decode_ourygo(url):
    # 独立按照站点 showV1 的位偏移解码，检查三个分区与重复张数。
    query = parse_qs(urlparse(url).query)
    assert query['ygotype'] == ['deck'] and query['v'] == ['1']
    code = query['d'][0]
    data = base64.urlsafe_b64decode(code + '=' * (-len(code) % 4))
    bits = ''.join(f'{byte:08b}' for byte in data)
    counts = [int(bits[:8], 2), int(bits[8:12], 2), int(bits[12:16], 2)]
    pos, result = 16, []
    for count in counts:
        cards = []
        for _ in range(count):
            amount, card = int(bits[pos:pos+2], 2), int(bits[pos+2:pos+29], 2)
            cards.extend([card] * amount)
            pos += 29
        result.append(Counter(cards))
    return result


def test_share_link_round_trip_including_partial_side():
    deck = Deck('玩家+甲&乙', (2, 1, 2), (9,), (3, 3, 4))
    assert decode_ourygo(deck_url(deck)) == [Counter(deck.main), Counter(deck.extra), Counter(deck.side)]
    assert deck.ydk().split('#main\n')[1].startswith('1\n2\n2\n')
    assert parse_qs(urlparse(deck_url(deck)).query)['name'] == [deck.player]


def test_inferred_side_uses_max_multiset_delta_and_replay_time():
    def raw(main, extra, ts):
        return {'start_time': ts, 'decks': [{'player': '甲', 'main': main, 'extra': extra}]}
    rounds = [raw([1, 2, 2, 8], [11], 300), raw([1, 1, 2, 3], [10], 100),
              raw([2, 2, 2, 9], [11], 200)]
    combined = consolidated_decks(rounds)[0]
    assert combined.main == (1, 1, 2, 3)
    assert combined.extra == (10,)
    assert combined.side == (2, 2, 8, 9, 11)
    # 初始已有但换出的 1/3/10 不再虚构成 Side，重复换入的 2/11 不跨局累加。


def test_labels_are_ordered_deduplicated_and_rules_reload(tmp_path):
    path = tmp_path / 'rules.json'
    path.write_text(json.dumps({'version': 1, 'rules': [
        {'label': '系列甲', 'cards': [123]}, {'label': '引擎乙', 'cards': [456, 999]},
        {'label': '系列甲', 'cards': [456]}, {'label': '不应命中', 'match': 'all', 'cards': [123, 999]},
    ]}), encoding='utf-8')
    result = classify(Deck('甲', (123,), (456,)), read_rules(path))
    assert result['name'] == '系列甲＋引擎乙'
    assert result['evidence'][1]['cards'] == [456]
    path.write_text('{"version":1,"rules":[]}', encoding='utf-8')
    assert classify(Deck('甲', (123,), (456,)), read_rules(path))['name'] == '未识别'
    path.write_text('{"version":1,"rules":[{"label":"x","cards":[true]}]}', encoding='utf-8')
    with pytest.raises(ValueError):
        read_rules(path)


def test_store_restart_duplicate_replay_and_outbox(tmp_path):
    store = ArchiveStore(tmp_path, {'1', '2'})
    match, active = store.begin('M#room', ['甲', '乙'])
    assert active
    first, second = replay(), replay(main_a=(123, 555), timestamp=200)
    store.save_replay(match, first)
    store.save_replay(match, first)
    store = ArchiveStore(tmp_path, {'1', '2'})
    store.recover_files(match)
    store.save_replay(match, second)
    store.finalize(match, True)
    store.finalize(match, True)
    assert len(store.replays(match)) == 2
    assert store.begin('M#room', ['甲', '乙'])[1] is False
    pending = store.pending()
    assert {row['recipient'] for row in pending} == {'1', '2'}
    store.delivered(pending[0]['id'], True)
    assert pending[0]['id'] not in {row['id'] for row in store.pending()}
    with zipfile.ZipFile(store.bundle(match)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        assert len(manifest['replays']) == 2
        assert manifest['combined_decks'][0]['side'] == [555]
        assert manifest['combined_decks'][0]['side_known'] is False
        assert len([name for name in bundle.namelist() if name.endswith('.ydk')]) == 6


def test_missing_or_unparseable_replay_is_explicit(tmp_path):
    store = ArchiveStore(tmp_path, {'1'})
    match, _ = store.begin('M#none', ['甲', '乙'])
    store.finalize(match, True)
    assert store.stats()['partial'] == 1
    assert '未收到录像' in store.pending()[0]['message']
    bad, _ = store.begin('M#bad', ['甲', '乙'])
    store.save_replay(bad, b'not-a-replay')
    store.finalize(bad, True)
    manifest = json.loads((tmp_path / bad / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['issues'] and manifest['replays'][0]['decks'] == []
    assert manifest['complete'] is False
    assert (tmp_path / bad / manifest['replays'][0]['file']).read_bytes() == b'not-a-replay'


def test_duplicate_events_do_not_start_two_watchers_and_shutdown_preserves_active(tmp_path):
    async def run():
        started = asyncio.Event()
        calls = []
        async def watcher(room, identity, ended, save, **kwargs):
            calls.append(room)
            kwargs['connected']()
            await save(replay())
            started.set()
            await ended.wait()
            return True
        async def send(*args):
            pass
        runtime = ArchiveRuntime(tmp_path, tmp_path/'credentials.json', {'1'}, send, join_interval=0.001, watch_room=watcher)
        async def identity():
            return '观战', 42
        runtime.credentials.get = identity
        room = {'id': 'M#test', 'users': [{'username': '甲'}, {'username': '乙'}], 'options': {}}
        await runtime.process({'event': 'init', 'data': [room]})
        await runtime.process({'event': 'create', 'data': room})
        await asyncio.wait_for(started.wait(), 2)
        state = runtime.rooms[room['id']]
        await runtime.process({'event': 'delete', 'data': room['id']})
        await asyncio.wait_for(state.task, 2)
        assert calls == ['M#test']
        assert runtime.store.stats()['complete'] == 1
        room['id'] = 'M#ongoing'
        started.clear()
        await runtime.process({'event': 'create', 'data': room})
        await asyncio.wait_for(started.wait(), 2)
        await runtime.close()
        assert runtime.store.stats()['active'] == 1
    asyncio.run(run())


def test_delivery_failure_retries_without_resending_successful_recipient(tmp_path):
    async def run():
        sent = []
        fail = True
        async def send(uid, text):
            if uid == '1' and fail:
                raise RuntimeError('network')
            sent.append(uid)
        runtime = ArchiveRuntime(tmp_path, tmp_path/'creds', {'1', '2'}, send)
        mid, _ = runtime.store.begin('M#x', ['甲', '乙'])
        runtime.store.save_replay(mid, replay())
        runtime.store.finalize(mid, True)
        await runtime.deliver_once()
        assert sent == ['2']
        fail = False
        with runtime.store.connect() as db:
            db.execute('UPDATE deliveries SET retry_at=0')
        await runtime.deliver_once()
        assert sent == ['2', '1']
        assert runtime.store.stats()['pending_messages'] == 0
    asyncio.run(run())


def test_protocol_reassembles_fragments_drains_after_delete_and_closes(tmp_path):
    async def run():
        ended = asyncio.Event()
        got_join, closed = asyncio.Event(), asyncio.Event()
        async def server(reader, writer):
            try:
                await reader.readexactly(94)
                got_join.set()
                writer.write(b'\x02\x00\x13\x07')
                await writer.drain()
                ended.set()
                await asyncio.sleep(0.02)
                data = replay()
                packet = struct.pack('<HB', len(data)+1, 0x17) + data
                for part in (packet[:1], packet[1:17], packet[17:]):
                    writer.write(part)
                    await writer.drain()
                    await asyncio.sleep(0.01)
                writer.write(b'\x01\x00\x16')
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()
        listener = await asyncio.start_server(server, '127.0.0.1', 0)
        received = []
        async def save(data):
            received.append(data)
        try:
            port = listener.sockets[0].getsockname()[1]
            complete = await asyncio.wait_for(watch('M#x', ('观战', 42), ended, save,
                                                     host='127.0.0.1', port=port, drain_seconds=1), 3)
            assert complete and received == [replay()]
            await asyncio.wait_for(closed.wait(), 1)
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_credentials_errors_never_include_secret(tmp_path):
    path = tmp_path/'credentials.json'
    path.write_text('{broken super-secret', encoding='utf-8')
    with pytest.raises(CredentialError) as exc:
        asyncio.run(Credentials(path).get())
    assert 'super-secret' not in str(exc.value)
    path.write_text(json.dumps({'username': '观战', 'u16_secret': 42}), encoding='utf-8')
    assert asyncio.run(Credentials(path).get()) == ('观战', 42)
    assert len(join_packets('观战', 42, 'M#room', 0x1362)) == 94


def test_messages_only_contain_match_and_players_deck_links():
    messages = build_messages('a'*24, ['甲', '乙'], [{'decks': [
        {'player': '甲', 'main': [1], 'extra': [2]}, {'player': '乙', 'main': [3], 'extra': []}]}], [], True, [])
    text = '\n'.join(messages)
    assert text.count('http://deck.ourygo.top/') == 2
    assert messages == ['甲 vs 乙\n甲：' + deck_url(Deck('甲', (1,), (2,)))
                        + '\n乙：' + deck_url(Deck('乙', (3,), ()))]


def test_failed_notification_names_the_actual_error_without_archive_details():
    assert build_messages('a'*24, ['甲', '乙'], [], [
        '观战异常：ConnectionResetError', '未确认正常结束信号，归档可能缺少小局',
        '未收到录像，不能提取卡组'], False, []) == [
            '甲 vs 乙\n提取失败：ConnectionResetError（观战连接被重置）']


def test_restart_recovers_unindexed_replay_and_finalizes_missing_room(tmp_path):
    async def run():
        store = ArchiveStore(tmp_path, {'1'})
        mid, _ = store.begin('M#lost', ['甲', '乙'])
        (tmp_path / mid / 'orphan.yrp').write_bytes(replay())
        async def send(*args):
            pass
        runtime = ArchiveRuntime(tmp_path, tmp_path / 'credentials', {'1'}, send)
        await runtime.process({'event': 'init', 'data': []})
        assert runtime.store.stats()['partial'] == 1
        assert len(runtime.store.replays(mid)) == 1
        assert runtime.store.pending()[0]['message'].count('http://deck.ourygo.top/') == 2
        manifest = json.loads((tmp_path / mid / 'manifest.json').read_text(encoding='utf-8'))
        assert any('重启后' in issue for issue in manifest['issues'])
        await runtime.close()
    asyncio.run(run())


def test_forbidden_room_waits_until_end_without_joining(tmp_path):
    async def run():
        async def send(*args):
            pass
        async def forbidden(*args, **kwargs):
            pytest.fail('禁止观战的房间不应连接')
        runtime = ArchiveRuntime(tmp_path, tmp_path / 'credentials', {'1'}, send, watch_room=forbidden)
        room = {'id': 'M#private', 'users': [{'username': '甲'}, {'username': '乙'}],
                'options': {'no_watch': True}}
        await runtime.process({'event': 'init', 'data': [room]})
        task = runtime.rooms[room['id']].task
        assert runtime.store.pending() == []
        await runtime.process({'event': 'delete', 'data': room['id']})
        await asyncio.wait_for(task, 2)
        assert runtime.store.stats()['partial'] == 1
        assert '禁止观战' in runtime.store.pending()[0]['message']
        await runtime.close()
    asyncio.run(run())


def test_queued_room_finishes_immediately_and_slot_is_not_leaked(tmp_path):
    async def run():
        async def send(*args):
            pass
        runtime = ArchiveRuntime(tmp_path, tmp_path/'credentials', {'1'}, send, concurrency=1)
        await runtime.semaphore.acquire()
        room = {'id': 'M#queued', 'users': [{'username': '甲'}, {'username': '乙'}]}
        await runtime.process({'event': 'create', 'data': room})
        task = runtime.rooms[room['id']].task
        await runtime.process({'event': 'delete', 'data': room['id']})
        await asyncio.wait_for(task, 2)
        assert 'QueueFull（排队时对局已结束）' in runtime.store.pending()[0]['message']
        assert runtime.semaphore.locked()
        runtime.semaphore.release()
        async with runtime.slot(asyncio.Event()) as available:
            assert available and runtime.semaphore.locked()
        assert not runtime.semaphore.locked()
        await runtime.close()
    asyncio.run(run())


def test_rank_boundary_cache_refresh_and_unknown_are_not_false_negatives():
    async def run():
        values = {'甲': 500, '乙': 501, '丙': 900}
        calls = []
        async def fetch(name):
            calls.append(name)
            return values[name]
        ranks = RankFilter(fetch_rank=fetch)
        assert await ranks.eligible(['乙', '甲'])
        assert not await ranks.eligible(['乙', '丙'])
        assert calls.count('乙') == 1
        values['甲'] = 501
        ranks.cache['甲'] = (500, -1000)
        assert not await ranks.eligible(['甲', '乙'])
        with pytest.raises(ValueError):
            await ranks.eligible(['未知', '乙'])
        values['丙'] = 1
        ranks.cache.pop('丙')
        assert await ranks.eligible(['未知', '丙'])
    asyncio.run(run())


def test_outside_top500_never_joins_or_notifies(tmp_path):
    async def run():
        async def send(*args):
            pytest.fail('排名范围外不发归档私聊')
        async def watch_room(*args, **kwargs):
            pytest.fail('排名范围外不能观战')
        async def rank(name):
            return 501
        runtime = ArchiveRuntime(tmp_path, tmp_path/'credentials', {'1'}, send,
                                 rank_filter=RankFilter(fetch_rank=rank), watch_room=watch_room)
        room = {'id': 'M#excluded', 'users': [{'username': '甲'}, {'username': '乙'}]}
        await runtime.process({'event': 'create', 'data': room})
        await asyncio.wait_for(runtime.rooms[room['id']].task, 2)
        assert runtime.store.stats()['excluded'] == 1
        assert runtime.store.pending() == []
        await runtime.close()
    asyncio.run(run())


def test_failed_handshakes_share_cooldown_and_only_one_recovery_probe(tmp_path):
    async def run():
        from hikari_bot.services.mycard_archive.admission import JoinGate
        calls = []
        third, probing, release_probe = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def watcher(room, identity, ended, save, **kwargs):
            calls.append(room)
            if len(calls) <= 3:
                if len(calls) == 3:
                    third.set()
                raise ConnectionResetError()
            probing.set()
            await release_probe.wait()
            kwargs['connected']()
            await ended.wait()
            return True
        async def send(*args):
            pass
        runtime = ArchiveRuntime(tmp_path, tmp_path/'creds', {'1'}, send,
                                 watch_room=watcher, join_interval=0)
        runtime.join_gate = JoinGate(interval=0, cooldown=0.15)
        async def identity():
            return '观战', 42
        runtime.credentials.get = identity
        try:
            for index in range(8):
                await runtime.add({'id': f'M#{index}', 'users': [{'username': '甲'}, {'username': '乙'}]})
            await asyncio.wait_for(third.wait(), 2)
            await asyncio.sleep(0.05)
            assert len(calls) == 3
            await asyncio.wait_for(probing.wait(), 2)
            await asyncio.sleep(0.05)
            assert len(calls) == 4
            release_probe.set()
            # 成功后释放握手锁，其他对局可并行观战，不必等这一场结束。
            async with asyncio.timeout(2):
                while len(calls) < 8:
                    await asyncio.sleep(0.01)
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('accept', [False, True])
def test_handshake_deadline_does_not_limit_accepted_match(accept):
    async def run():
        from hikari_bot.services.mycard_archive.spectator import HandshakeTimeoutError
        async def server(reader, writer):
            try:
                await reader.readexactly(94)
                if accept:
                    writer.write(b'\x02\x00\x13\x07')
                    await writer.drain()
                    await asyncio.sleep(0.12)
                    writer.write(b'\x01\x00\x16')
                    await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
        listener = await asyncio.start_server(server, '127.0.0.1', 0)
        async def save(data):
            pass
        try:
            task = watch('M#x', ('观战', 42), asyncio.Event(), save,
                         host='127.0.0.1', port=listener.sockets[0].getsockname()[1], handshake_seconds=0.05)
            if accept:
                assert await asyncio.wait_for(task, 1)
            else:
                with pytest.raises(HandshakeTimeoutError):
                    await asyncio.wait_for(task, 1)
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(run())


def test_join_gate_releases_cancelled_waiters_and_rooms_ending_in_cooldown():
    async def run():
        from hikari_bot.services.mycard_archive.admission import JoinGate
        gate = JoinGate(interval=0, cooldown=60)
        async with gate.attempt(asyncio.Event()) as accepted:
            async def queued(ended):
                async with gate.attempt(ended) as probe:
                    assert probe is None
            ended = asyncio.Event()
            waiter = asyncio.create_task(queued(ended))
            await asyncio.sleep(0)
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            assert gate.lock.locked()
            accepted()
        # 三个失败后进入冷却；冷却中的房间结束不再计为握手失败。
        for _ in range(3):
            async with gate.attempt(asyncio.Event()):
                pass
        assert gate.failures == 3 and gate.remaining > 59
        ended = asyncio.Event()
        waiter = asyncio.create_task(queued(ended))
        await asyncio.sleep(0)
        ended.set()
        await asyncio.wait_for(waiter, 1)
        assert gate.failures == 3 and not gate.lock.locked()
        waiter = asyncio.create_task(queued(asyncio.Event()))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not gate.lock.locked()
    asyncio.run(run())
