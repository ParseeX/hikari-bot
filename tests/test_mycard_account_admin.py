import asyncio
import json

import pytest

from hikari_bot.services.mycard_archive.account_admin import StaleAccounts, finish_change
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime
from hikari_bot.services.mycard_archive.spectator import Credentials, CredentialError
from test_mycard_accounts import credential
from test_mycard_scheduling import ignore, until


def test_add_validate_duplicate_shared_password_and_restart(tmp_path, monkeypatch):
    async def run():
        shared = tmp_path / 'password.json'
        shared.write_text('{"password":"test-secret"}')
        initial = credential(tmp_path / 'one.json', 'one')
        initial.write_text(json.dumps({'username': 'one', 'u16_secret': 42, 'password_file': 'password.json'}))
        runtime = ArchiveRuntime(tmp_path / 'archive', initial, set(), ignore, concurrency=1)
        manager = runtime.account_admin
        async def login(self):
            data = json.loads(self.path.read_text('utf-8'))
            assert data['password_file'] == str(shared)
            assert 'password' not in data
            if data['username'] == 'bad':
                raise CredentialError('登录被拒绝')
            return data['username'], 42
        monkeypatch.setattr(Credentials, 'get', login)
        original = manager.revision
        with pytest.raises(CredentialError):
            await manager.add(original, 'bad')
        assert not list((manager.root / 'collector-credentials').glob('*.json'))
        assert manager.revision == original
        data = await manager.add(original, 'two')
        assert len(data['accounts']) == 2 and data['concurrency'] == 1
        assert data['effective_concurrency'] == 1
        text = json.dumps(data)
        assert all(secret not in text for secret in ('test-secret', 'password_file', str(shared), 'u16_secret', 'token'))
        with pytest.raises(StaleAccounts):
            await manager.configure(original, 2)
        with pytest.raises(ValueError, match='已在'):
            await manager.add(data['revision'], 'two')
        data = await manager.configure(data['revision'], 2)
        assert data['effective_concurrency'] == 2
        await runtime.close()
        resumed = ArchiveRuntime(manager.root, initial, set(), ignore, concurrency=1)
        try:
            assert resumed.concurrency == 2
            assert [a['username'] for a in resumed.account_admin.config['accounts']] == ['one', 'two']
            assert resumed.account_admin.config['shared_password_file'] == str(shared)
            admin = resumed.account_admin
            for entry in list(admin.config['accounts']):
                await admin.remove(admin.revision, entry['id'])
            assert resumed.concurrency == 0
            restored = await admin.add(admin.revision, 'three')
            assert restored['concurrency'] == 2 and restored['effective_concurrency'] == 1
            assert restored['accounts'][0]['username'] == 'three'
        finally:
            await resumed.close()
    asyncio.run(run())


def test_shrink_remove_live_account_waits_for_close_and_empty_restart(tmp_path):
    async def run():
        files = [credential(tmp_path / f'{name}.json', name) for name in ('one', 'two')]
        closing, allowed = asyncio.Event(), asyncio.Event()
        live = set()
        async def watch(room, identity, ended, save, **kwargs):
            assert identity[0] not in live
            live.add(identity[0])
            kwargs['connected']()
            try:
                await asyncio.Event().wait()
            finally:
                closing.set()
                await allowed.wait()
                live.remove(identity[0])
        runtime = ArchiveRuntime(tmp_path / 'archive', files[0], set(), ignore,
                                 credential_files=files, concurrency=2, join_interval=0, watch_room=watch)
        manager = runtime.account_admin
        try:
            for room in ('a', 'b', 'c'):
                await runtime.add({'id': room, 'users': [{'username': room}, {'username': 'opponent'}]})
            await until(lambda: len(live) == 2)
            snapshot = manager.snapshot()
            assert sum(a['state'] == 'watching' for a in snapshot['accounts']) == 2
            shrink = asyncio.create_task(manager.configure(snapshot['revision'], 1))
            await asyncio.wait_for(closing.wait(), 3)
            assert not shrink.done() and len(live) == 2
            allowed.set()
            await asyncio.wait_for(shrink, 3)
            assert len(live) == 1
            # 扩容恢复排队中的任务，而非复制已有账号的连接。
            await manager.configure(manager.revision, 2)
            await until(lambda: len(live) == 2)
            remove_id = manager.snapshot()['accounts'][0]['id']
            allowed.clear(); closing.clear()
            removal = asyncio.create_task(manager.remove(manager.revision, remove_id))
            await asyncio.wait_for(closing.wait(), 3)
            assert not removal.done()
            allowed.set()
            result = await asyncio.wait_for(removal, 3)
            assert len(result['accounts']) == 1 and len(live) <= 1
            await manager.remove(result['revision'], result['accounts'][0]['id'])
            assert not live and runtime.concurrency == 0
            assert (manager.root / 'archive.sqlite3').exists()
            assert all(path.exists() for path in files)
        finally:
            allowed.set()
            await runtime.close()
        resumed = ArchiveRuntime(manager.root, files[0], set(), ignore, concurrency=2)
        assert resumed.concurrency == 0 and not resumed.accounts.accounts
        await resumed.close()
    asyncio.run(run())


def test_request_cancellation_finishes_saved_mutation():
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        done = []
        async def change():
            entered.set()
            await finish.wait()
            done.append(True)
        task = asyncio.create_task(finish_change(change()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert done == [True]
    asyncio.run(run())
