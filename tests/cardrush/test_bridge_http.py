"""使用真实环回 HTTP 验证鉴权及参数边界，不需要连接手机。"""
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx


def test_bridge_limits_requests_and_preserves_safe_errors(monkeypatch):
    base = Path('scripts/jihuanshe_bridge')
    monkeypatch.setitem(sys.modules, 'frida', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'frida_tools', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'phone', SimpleNamespace(BASE=base, Phone=object))
    spec = importlib.util.spec_from_file_location('bridge_http_test', base / 'worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    class Worker:
        lock = threading.Lock()
        def versions(self, name):
            calls.append(name)
            return {'versions': []}
        def prices(self, ids):
            raise RuntimeError('private upstream details must not escape')
        def listings(self, version_id, pages):
            return {'pages': [{'current_page': p, 'entries': []} for p in pages]}
    server = module.create_server(Worker(), 'test-bridge-key', 0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            body = {'name_jp': '原石の皇脈'}
            assert client.post('/v1/versions', json=body).status_code == 401
            client.headers['Authorization'] = 'Bearer test-bridge-key'
            assert client.post('/v1/versions', json=body).json() == {'versions': []}
            assert calls == ['原石の皇脈']
            for ids in ([1, 1], [True], [-1], [1.5], [], list(range(1, 22)), [[1]]):
                assert client.post('/v1/prices', json={'version_ids': ids}).status_code == 400
            assert client.post('/v1/versions', json={'name_jp': ' '}).status_code == 400
            assert client.post('/v1/versions', content='x' * 8193).status_code == 400
            assert client.post('/execute', json=body).status_code == 404
            assert client.post('/v1/listings', json={'card_version_id': 1, 'pages': [1, 2]}).status_code == 200
            for pages in ([0], [True], [101], [1, 1], list(range(1, 7))):
                assert client.post('/v1/listings', json={'card_version_id': 1, 'pages': pages}).status_code == 400
            failed = client.post('/v1/prices', json={'version_ids': [1]})
            assert failed.status_code == 503 and failed.json() == {'error': 'unavailable'}
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
