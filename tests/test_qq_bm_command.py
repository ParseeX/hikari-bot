"""BM 连写命令通过真实官方入口进入音频解析与发送。"""
import subprocess
import sys


def test_compact_and_spaced_bm_reach_audio_parser_in_private_and_group_chats():
    script = r'''
import asyncio, sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
sys.path.insert(0, 'tests')
import nonebot
nonebot.logger.remove()
nonebot.init(driver='~aiohttp', _env_file=[], log_level='ERROR')
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.platform import Media, Result
from test_qq_migration import bot, event

async def run():
    links = SimpleNamespace(parse=AsyncMock(return_value=Result(media=(Media('audio', b'audio', 'test.mp3'),))))
    config = PilotConfig('app', 'fake', users=frozenset({'u'}), groups=frozenset({'g'}))
    handler = plugin.register(config, queries=Queries(None, links=links)).handlers[0].call
    for kind in ['c2c', 'group', 'group_at']:
        for text, expected in [
            ('bmBV1eTHN6bEMc', 'bm BV1eTHN6bEMc'),
            ('bm BV1eTHN6bEMc', 'bm BV1eTHN6bEMc'),
            ('/bmBV1eTHN6bEMc 2', 'bm BV1eTHN6bEMc 2'),
            (' /bm BV1eTHN6bEMc 2 ', 'bm BV1eTHN6bEMc 2'),
        ]:
            links.parse.reset_mock()
            client = bot()
            await handler(client, event(kind, id=kind + text, text=text))
            links.parse.assert_awaited_once_with(expected)
            sender = client.send_to_c2c if kind == 'c2c' else client.send_to_group
            sender.assert_awaited_once()
            message = sender.await_args.kwargs['message']
            assert len(message) == 1 and message[0].type == 'file_audio'

asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-4000:] + result.stdout[-1000:]
