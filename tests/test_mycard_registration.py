import asyncio
from contextlib import asynccontextmanager
import json
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from yarl import URL

from hikari_bot.services.mycard_archive import registration
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime
from hikari_bot.services.mycard_archive.spectator import Credentials
from test_mycard_scheduling import ignore


def transport(monkeypatch, *, status=200, result=None, error=None):
    calls = []
    class Response:
        async def __aenter__(self):
            if error:
                raise error
            return self
        async def __aexit__(self, *_): pass
        async def json(self, **kwargs): return result
    Response.status = status
    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
    @asynccontextmanager
    async def session(server):
        assert server in ('primary', 'backup')
        yield Session()
    monkeypatch.setattr(registration, 'registration_session', session)
    return calls


@pytest.mark.parametrize('server', ['primary', 'backup'])
def test_registration_uses_shared_password_and_adds_account_without_increasing_limit(tmp_path, monkeypatch, server):
    async def run():
        shared = tmp_path / 'password.json'
        shared.write_text('{"password":"private-password"}')
        original = tmp_path / 'account.json'
        original.write_text(json.dumps({'username': 'original', 'password_file': 'password.json', 'token': 'old'}))
        runtime = ArchiveRuntime(tmp_path / 'archive', original, set(), ignore)
        calls = transport(monkeypatch, result={'token': 'private-token'})
        async def identity(self):
            data = json.loads(self.path.read_text())
            if data['username'] == 'new':
                assert data['token'] == 'private-token'
                assert data['password_file'] == str(shared)
            return data['username'], 42
        monkeypatch.setattr(Credentials, 'get', identity)
        try:
            admin = runtime.account_admin
            result = await admin.register(admin.revision, 'new', 'new@example.com', server, 'a' * 32)
            assert result['registration']['added'] and result['registration']['created']
            assert result['concurrency'] == 1 and len(result['accounts']) == 2
            assert len(calls) == 1
            url, kwargs = calls[0]
            assert url == registration.API + '/signup' and kwargs['allow_redirects'] is False
            assert kwargs['json'] == {'username': 'new', 'email': 'new@example.com', 'password': 'private-password'}
            encoded = json.dumps(result) + admin.registrations.path.read_text()
            assert 'private-token' not in encoded and 'private-password' not in encoded
            with pytest.raises(ValueError, match='已在'):
                await admin.register(admin.revision, 'new', 'new@example.com', server, 'b' * 32)
            assert len(calls) == 1
        finally:
            await runtime.close()
    asyncio.run(run())


def test_successful_signup_failed_add_is_reported_as_created_and_not_retried(tmp_path, monkeypatch):
    async def run():
        shared = tmp_path / 'password.json'; shared.write_text('{"password":"private-password"}')
        initial = tmp_path / 'one.json'; initial.write_text(json.dumps({'username': 'one', 'password_file': str(shared)}))
        runtime = ArchiveRuntime(tmp_path / 'archive', initial, set(), ignore)
        calls = transport(monkeypatch, result={'token': 'private-token'})
        runtime.account_admin.add = AsyncMock(side_effect=OSError('disk'))
        try:
            admin = runtime.account_admin
            result = await admin.register(admin.revision, 'new', 'new@example.com', 'backup', 'a' * 32)
            assert result['registration']['created'] and not result['registration']['added']
            with pytest.raises(registration.RegistrationError, match='已有注册提交记录'):
                await registration.Registrations(admin.root).create('new', 'new@example.com', shared, 'primary', 'b' * 32)
            assert len(calls) == 1
        finally:
            await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('status,result,uncertain', [(429, {}, False), (400, {'message': 'i_email_exists'}, False),
    (403, {'error': 'captcha'}, False), (500, {}, True), (200, {}, True)])
def test_rejections_are_sanitized_and_ambiguous_results_are_not_retried(tmp_path, monkeypatch, status, result, uncertain):
    async def run():
        shared = tmp_path / 'password.json'; shared.write_text('{"password":"private-password"}')
        calls = transport(monkeypatch, status=status, result={**result, 'password': 'private-password', 'debug': 'private-token'})
        manager = registration.Registrations(tmp_path)
        with pytest.raises(registration.RegistrationError) as error:
            await manager.create('new', 'new@example.com', shared, 'primary', 'a' * 32)
        assert error.value.uncertain is uncertain
        assert 'private-password' not in str(error.value) and 'private-token' not in str(error.value)
        with pytest.raises(registration.RegistrationError):
            await registration.Registrations(tmp_path).create('new', 'new@example.com', shared, 'backup', ('b' if uncertain else 'a') * 32)
        assert len(calls) == 1
    asyncio.run(run())


def test_timeout_is_uncertain_and_invalid_email_does_not_submit(tmp_path, monkeypatch):
    async def run():
        shared = tmp_path / 'password.json'; shared.write_text('{"password":"private-password"}')
        calls = transport(monkeypatch, error=TimeoutError('private-token'))
        manager = registration.Registrations(tmp_path)
        with pytest.raises(registration.RegistrationError, match='有效邮箱'):
            await manager.create('new', 'bad-email', shared, 'primary', 'a' * 32)
        assert calls == [] and not manager.path.exists()
        with pytest.raises(registration.RegistrationError) as error:
            await manager.create('new', 'new@example.com', shared, 'backup', 'a' * 32)
        assert error.value.uncertain and 'private-token' not in str(error.value)
        with pytest.raises(registration.RegistrationError):
            await manager.create('new', 'new@example.com', shared, 'primary', 'b' * 32)
        assert len(calls) == 1
    asyncio.run(run())


def test_forward_performs_tls_with_official_hostname_and_rejects_other_destinations():
    async def run():
        create = AsyncMock(return_value=(object(), 'protocol'))
        connector = SimpleNamespace(_loop=SimpleNamespace(create_unix_connection=create), _factory=object(), path='/test/socket')
        request = SimpleNamespace(url=URL(registration.API + '/signup'))
        result = await registration.TLSForwardConnector._create_connection(connector, request, [], None)
        assert result == 'protocol'
        options = create.call_args.kwargs
        assert options['server_hostname'] == 'sapi.moecube.com'
        assert options['ssl'].verify_mode == ssl.CERT_REQUIRED and options['ssl'].check_hostname
        with pytest.raises(registration.RegistrationError):
            await registration.TLSForwardConnector._create_connection(connector, SimpleNamespace(url=URL('https://evil.invalid/')), [], None)
        assert create.await_count == 1
    asyncio.run(run())
