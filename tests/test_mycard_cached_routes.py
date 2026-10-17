"""确认统计接口已注册，且不会落入短链接兜底路由。"""
import importlib.util
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from hikari_bot.core.config import settings as app_settings


@pytest.fixture
def cached_web(tmp_path):
    source = Path(__file__).parents[1] / 'hikari_bot/plugins/web/routes/mycard.py'
    spec = importlib.util.spec_from_file_location('mc_cached_routes_test', source)
    module = importlib.util.module_from_spec(spec)
    settings = replace(app_settings, mycard_archive_dir=tmp_path, card_catalog_path=tmp_path / 'cards.sqlite3')
    with patch('hikari_bot.core.config.settings', settings):
        spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app) as client:
        yield module, client


@pytest.mark.parametrize(('path', 'method'), [
    ('/mc/api/leaderboard?page=10', 'read_page'),
    ('/mc/api/deck-stats', 'read'),
    ('/mc/api/card-stats', 'read_cards'),
    ('/mc/api/match-stats', 'read_matchups'),
])
def test_statistics_use_cache_and_report_unavailable(cached_web, monkeypatch, path, method):
    module, client = cached_web
    monkeypatch.setattr(module.series_stats, method, lambda *args: None)
    response = client.get(path)
    assert response.status_code == 503
    assert response.headers['cache-control'] == 'no-store'
    monkeypatch.setattr(module.series_stats, method, lambda *args: b'{"total":100}')
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {'total': 100}


def test_invalid_page_does_not_fall_back_to_short_link(cached_web):
    _, client = cached_web
    assert client.get('/mc/api/leaderboard?page=0').status_code == 422
    assert client.get('/mc/api/leaderboard?page=11').status_code == 422
