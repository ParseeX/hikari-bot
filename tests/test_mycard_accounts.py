import asyncio
import json
from collections import Counter

import pytest

from hikari_bot.core.config import ConfigurationError, _mycard_credential_files
from hikari_bot.services.mycard_archive.accounts import AccountPool
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime
from hikari_bot.services.mycard_archive.spectator import CredentialError, Credentials
from test_mycard_scheduling import ignore, until


def credential(path, username):
    path.write_text(json.dumps({'username': username, 'u16_secret': 42}), encoding='utf-8')
    return path


@pytest.mark.parametrize('duplicate', [False, True])
def test_runtime_assigns_one_room_per_distinct_account(tmp_path, duplicate):
    async def run():
        files = [credential(tmp_path / 'a.json', 'account-a'),
                 credential(tmp_path / 'b.json', 'account-a' if duplicate else 'account-b')]
        release = {room: asyncio.Event() for room in ('one', 'two', 'three')}
        live = Counter()
        calls = []
        async def watch(room, identity, ended, save, **kwargs):
            user = identity[0]
            live[user] += 1
            assert live[user] == 1
            calls.append((room, user))
            kwargs['connected']()
            try:
                await release[room].wait()
                return True
            finally:
                live[user] -= 1
        runtime = ArchiveRuntime(tmp_path / 'archive', files[0], set(), ignore,
                                 credential_files=files, concurrency=9, join_interval=0, watch_room=watch)
        assert runtime.concurrency == 2
        try:
            for room in release:
                await runtime.add({'id': room, 'users': [{'username': room}, {'username': 'other'}]})
            expected = 1 if duplicate else 2
            await until(lambda: len(calls) == expected)
            await asyncio.sleep(0.05)
            assert len(calls) == expected
            release[calls[0][0]].set()
            await until(lambda: len(calls) == expected + 1)
            for event in release.values(): event.set()
            await until(lambda: not runtime.rooms)
            assert len(calls) == 3 and not runtime.accounts.active_users
        finally:
            await runtime.close()
        assert not any(live.values())
    asyncio.run(run())


def test_bad_and_slow_credentials_do_not_block_healthy_account(tmp_path):
    async def run():
        slow_started = asyncio.Event()
        class Slow:
            async def get(self):
                slow_started.set()
                await asyncio.Event().wait()
        class Bad:
            calls = 0
            async def get(self):
                self.calls += 1
                raise CredentialError('unavailable')
        bad = Bad()
        pool = AccountPool([Slow(), bad, Credentials(credential(tmp_path / 'good.json', 'good'))])
        try:
            for _ in range(2):
                async with asyncio.timeout(1):
                    async with pool.lease(asyncio.Event()) as (_, identity):
                        assert identity[0] == 'good'
            assert slow_started.is_set() and bad.calls == 1
            assert not pool.active_users
        finally:
            await pool.close()
        assert all(a.checking is None or a.checking.done() for a in pool.accounts)
    asyncio.run(run())


def test_waiting_lease_cancel_and_ended_room_do_not_leak_account(tmp_path):
    async def run():
        pool = AccountPool([Credentials(credential(tmp_path / 'one.json', 'one'))])
        entered = asyncio.Event()
        release = asyncio.Event()
        async def hold():
            async with pool.lease(asyncio.Event()):
                entered.set()
                await release.wait()
        holder = asyncio.create_task(hold())
        await entered.wait()
        ended = asyncio.Event()
        async def waiting():
            async with pool.lease(ended) as lease:
                assert lease is None
        cancelled = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        other = asyncio.create_task(waiting())
        ended.set()
        await asyncio.wait_for(other, 1)
        assert pool.active_users == {'one'}
        holder.cancel()
        await asyncio.gather(holder, return_exceptions=True)
        assert not pool.active_users
        async with asyncio.timeout(1):
            async with pool.lease(asyncio.Event()) as (_, identity):
                assert identity[0] == 'one'
        await pool.close()
    asyncio.run(run())


def test_rejected_account_cools_down_without_blocking_other_account(tmp_path):
    async def run():
        files = [credential(tmp_path / 'a.json', 'a'), credential(tmp_path / 'b.json', 'b')]
        pool = AccountPool([Credentials(path) for path in files], retry_seconds=0.03, refresh_seconds=0.01)
        try:
            async with pool.lease(asyncio.Event()) as (bad, identity):
                rejected = identity[0]
                pool.invalidate(bad)
            async with pool.lease(asyncio.Event()) as (_, identity):
                assert identity[0] != rejected
            await asyncio.sleep(0.04)
            async with pool.lease(asyncio.Event()) as (recovered, identity):
                if identity[0] == rejected:
                    assert not recovered.failed
                else:
                    async with asyncio.timeout(1):
                        async with pool.lease(asyncio.Event()) as (recovered, identity):
                            assert identity[0] == rejected and not recovered.failed
        finally:
            await pool.close()
    asyncio.run(run())


def test_changed_file_cannot_duplicate_an_active_username(tmp_path):
    async def run():
        first = credential(tmp_path / 'a.json', 'a')
        second = credential(tmp_path / 'b.json', 'b')
        pool = AccountPool([Credentials(first), Credentials(second)], refresh_seconds=0.01)
        try:
            async with pool.lease(asyncio.Event()) as (_, identity):
                active = identity[0]
                credential(second if active == 'a' else first, active)
                await asyncio.sleep(0.02)
                ended = asyncio.Event()
                async def wait():
                    async with pool.lease(ended) as lease:
                        assert lease is None
                waiter = asyncio.create_task(wait())
                await asyncio.sleep(0.04)
                assert not waiter.done()
                ended.set()
                await asyncio.wait_for(waiter, 1)
        finally:
            await pool.close()
    asyncio.run(run())


def test_credential_file_configuration_is_optional_and_deduplicated(monkeypatch, tmp_path):
    monkeypatch.delenv('MYCARD_CREDENTIALS_FILES', raising=False)
    assert _mycard_credential_files() == ()
    path = str(tmp_path / 'account.json')
    monkeypatch.setenv('MYCARD_CREDENTIALS_FILES', json.dumps([path, path]))
    assert _mycard_credential_files() == (tmp_path / 'account.json',)
    for value in ('[]', '{}', '[null]', '[""]', 'not-json'):
        monkeypatch.setenv('MYCARD_CREDENTIALS_FILES', value)
        with pytest.raises(ConfigurationError):
            _mycard_credential_files()
