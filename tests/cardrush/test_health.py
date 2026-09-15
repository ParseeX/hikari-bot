import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests

from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.health import check_connection


@pytest.mark.parametrize("outcome,expected", [
    ("valid", "成功"),
    ("empty", "未读取到卡价数据"),
    ("invalid", "解析失败"),
    (requests.exceptions.Timeout("secret"), "请求超时"),
    (requests.exceptions.ProxyError("secret"), "代理连接失败"),
    (requests.exceptions.ConnectionError("secret"), "网络连接失败"),
    ("http", "HTTP 403"),
])
def test_connection_uses_actual_client_configuration(monkeypatch, outcome, expected):
    calls = []
    client = CardrushClient("https://example.test", {"Accept": "text/html"},
                            {"https": "socks5h://proxy.test:1080"}, timeout=7)

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(outcome, Exception):
            raise outcome
        response = requests.Response()
        response.status_code = 403 if outcome == "http" else 200
        response._content = {
            "valid": Path("tests/cardrush/fixtures/cardrush_page.html").read_bytes(),
            "empty": b'<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"buyingPrices":[]}}}</script>',
        }.get(outcome, b"invalid secret")
        return response

    monkeypatch.setattr(requests, "get", get)
    result = asyncio.run(check_connection(client))
    assert expected in result
    assert "耗时" in result
    assert "secret" not in result
    assert calls == [(client.url, dict(params={"limit": 1, "page": 1},
                                     headers=client.headers, proxies=client.proxies, timeout=7))]


def test_startup_runs_once_and_manual_check_remains_available():
    # 隔离加载真实处理函数，避免导入插件时启动无关定时任务和数据库。
    tree = ast.parse(Path("hikari_bot/plugins/monitors/cardrush.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                 and node.name in {"_startup_price_check", "_manual_connection_check"}]
    for node in functions:
        node.decorator_list = []
    client = object()
    check = AsyncMock(return_value="Cardrush 连接测试 Failed")
    notify = AsyncMock()
    finish = AsyncMock()
    namespace = dict(Bot=object, _startup_checked=False, service=SimpleNamespace(client=client),
                     check_connection=check, log_message=AsyncMock(),
                     message_superusers=notify, cardrush_test=SimpleNamespace(finish=finish))
    exec(compile(ast.Module(body=functions, type_ignores=[]), "cardrush.py", "exec"), namespace)

    async def scenario():
        await asyncio.gather(namespace["_startup_price_check"](object()),
                             namespace["_startup_price_check"](object()))
        check.assert_awaited_once_with(client)
        notify.assert_awaited_once()
        await namespace["_manual_connection_check"]()
        assert check.await_count == 2
        finish.assert_awaited_once_with(check.return_value)

    asyncio.run(scenario())
