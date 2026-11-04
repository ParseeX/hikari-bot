import asyncio
import json

import pytest

from hikari_bot.services.mycard_archive.spectator import Credentials, CredentialError


def responses(monkeypatch, steps):
    calls = []
    class Response:
        def __init__(self, status, data): self.status, self.data = status, data
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def json(self): return self.data
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        def request(self, method, url, **kwargs):
            assert kwargs['allow_redirects'] is False
            expected, status, data = steps.pop(0)
            assert method == expected
            calls.append((method, url, kwargs))
            return Response(status, data)
        def get(self, url, **kwargs): return self.request('GET', url, **kwargs)
        def post(self, url, **kwargs): return self.request('POST', url, **kwargs)
    monkeypatch.setattr('hikari_bot.services.mycard_archive.spectator.aiohttp.ClientSession',
                        lambda **_: Session())
    return calls


def account(path, **extra):
    data = {'username': 'collector', 'password': 'private-password', 'token': 'old-token', **extra}
    path.write_text(json.dumps(data), encoding='utf-8')
    return Credentials(path)


INFO = {'username': 'collector', 'u16Secret': 42, 'active': True}


def test_valid_token_does_not_login_with_password(tmp_path, monkeypatch):
    source = account(tmp_path / 'credentials.json')
    calls = responses(monkeypatch, [('GET', 200, INFO)])
    assert asyncio.run(source.get()) == ('collector', 42)
    assert len(calls) == 1


def test_expired_token_logs_in_once_verifies_and_persists(tmp_path, monkeypatch):
    path = tmp_path / 'credentials.json'
    source = account(path)
    calls = responses(monkeypatch, [('GET', 401, {}), ('POST', 200, {'token': 'new-token'}), ('GET', 200, INFO)])
    async def run():
        assert await asyncio.gather(source.get(), source.get()) == [('collector', 42)] * 2
    asyncio.run(run())
    assert len(calls) == 3
    assert calls[1][2]['json'] == {'account': 'collector', 'password': 'private-password'}
    assert json.loads(path.read_text()) == {'username': 'collector', 'password': 'private-password', 'token': 'new-token'}
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('status', [400, 401, 403, 302])
def test_rejected_password_stops_retries_until_file_changes(tmp_path, monkeypatch, status):
    source = account(tmp_path / 'credentials.json', token='')
    calls = responses(monkeypatch, [('POST', status, {'error': 'private-password'})])
    for _ in range(2):
        with pytest.raises(CredentialError) as exc:
            asyncio.run(source.get())
        assert 'private-password' not in str(exc.value)
    assert len(calls) == 1


def test_auth_server_error_does_not_trigger_password_login(tmp_path, monkeypatch):
    source = account(tmp_path / 'credentials.json')
    calls = responses(monkeypatch, [('GET', 500, {})])
    with pytest.raises(CredentialError): asyncio.run(source.get())
    assert len(calls) == 1


@pytest.mark.parametrize('info', [{**INFO, 'username': 'other'}, {**INFO, 'active': False}, {**INFO, 'u16Secret': None}])
def test_invalid_new_identity_does_not_overwrite_credentials(tmp_path, monkeypatch, info):
    path = tmp_path / 'credentials.json'
    source = account(path)
    original = path.read_bytes()
    responses(monkeypatch, [('GET', 401, {}), ('POST', 200, {'token': 'new-token'}), ('GET', 200, info)])
    with pytest.raises(CredentialError): asyncio.run(source.get())
    assert path.read_bytes() == original


def test_missing_login_token_requires_manual_action(tmp_path, monkeypatch):
    source = account(tmp_path / 'credentials.json', token='')
    calls = responses(monkeypatch, [('POST', 200, {'captcha': 'challenge'})])
    for _ in range(2):
        with pytest.raises(CredentialError): asyncio.run(source.get())
    assert len(calls) == 1


def test_save_does_not_overwrite_a_concurrent_user_edit(tmp_path):
    path = tmp_path / 'credentials.json'
    source = account(path)
    original = path.read_text()
    updated = {'username': 'collector', 'password': 'changed-password'}
    path.write_text(json.dumps(updated))
    with pytest.raises(CredentialError): source._save_token(json.loads(original), 'new-token', original)
    assert json.loads(path.read_text()) == updated


def test_shared_password_is_used_without_copying_it_into_account_file(tmp_path, monkeypatch):
    path = tmp_path / 'account.json'
    shared = tmp_path / 'password.json'
    shared.write_text(json.dumps({'password': 'shared-secret'}))
    path.write_text(json.dumps({'username': 'collector', 'password_file': 'password.json'}))
    calls = responses(monkeypatch, [('POST', 200, {'token': 'new-token'}), ('GET', 200, INFO)])
    assert asyncio.run(Credentials(path).get()) == ('collector', 42)
    assert calls[0][2]['json']['password'] == 'shared-secret'
    saved = json.loads(path.read_text())
    assert saved == {'username': 'collector', 'password_file': 'password.json', 'token': 'new-token'}


def test_updating_shared_password_releases_manual_login_block(tmp_path, monkeypatch):
    path = tmp_path / 'account.json'
    shared = tmp_path / 'password.json'
    shared.write_text(json.dumps({'password': 'wrong'}))
    path.write_text(json.dumps({'username': 'collector', 'password_file': 'password.json'}))
    source = Credentials(path)
    calls = responses(monkeypatch, [('POST', 401, {}), ('POST', 200, {'token': 'new-token'}), ('GET', 200, INFO)])
    with pytest.raises(CredentialError): asyncio.run(source.get())
    shared.write_text(json.dumps({'password': 'correct-new-password'}))
    import os
    os.utime(shared, ns=(shared.stat().st_atime_ns, shared.stat().st_mtime_ns + 1000000))
    assert asyncio.run(source.get()) == ('collector', 42)
    assert calls[1][2]['json']['password'] == 'correct-new-password'


def setup_tool():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('mycard_credentials_setup',
        Path(__file__).resolve().parents[1] / 'scripts/mycard-credentials.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_tool_saves_one_shared_password_and_keeps_token_backup(tmp_path, monkeypatch):
    path = tmp_path / 'account.json'
    path.write_text(json.dumps({'token': 'old-token'}))
    shared = tmp_path / 'password.json'
    responses(monkeypatch, [('POST', 200, {'token': 'new-token'}), ('GET', 200, INFO)])
    asyncio.run(setup_tool().save_account(path, 'collector', 'shared-secret', shared))
    assert json.loads(shared.read_text()) == {'password': 'shared-secret'}
    assert json.loads(path.read_text()) == {'username': 'collector', 'token': 'new-token', 'password_file': 'password.json'}
    assert json.loads(next(tmp_path.glob('*.bak')).read_text()) == {'token': 'old-token'}
    assert not list(tmp_path.glob('.setup-*'))


def test_setup_failed_login_preserves_existing_token(tmp_path, monkeypatch):
    path = tmp_path / 'account.json'
    original = json.dumps({'token': 'old-token'})
    path.write_text(original)
    responses(monkeypatch, [('POST', 401, {})])
    with pytest.raises(CredentialError):
        asyncio.run(setup_tool().save_account(path, 'collector', 'wrong', tmp_path / 'password.json'))
    assert path.read_text() == original
    assert not (tmp_path / 'password.json').exists()
    assert not list(tmp_path.glob('.setup-*'))
