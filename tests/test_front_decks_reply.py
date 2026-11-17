"""前排列表共用网页排序；姓名链接编码、缺卡组、失败和三种聊天入口。"""
import asyncio
import subprocess
import sys
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from hikari_bot.services.mycard_front import FrontDecks
from hikari_bot.qq_official.front_decks import render_front_decks
from hikari_bot.qq_official.output_templates import default, draft, upgrade_spec

ROOT = 'front_decks.availability_1ddd1afd'


def snapshot():
    names = ['灵sama', '亚翁', 'a](https://evil.example) #?&'] + [f'玩家{i}' for i in range(4, 26)]
    return {'ranking_order': names, 'players': [
        {'player': name, 'rank': 999-i, 'deck': None if i == 4 else {'name': '' if i == 5 else '狱神奥义耀圣'}}
        for i, name in reversed(list(enumerate(names)))]}


def test_snapshot_order_twenty_links_missing_deck_and_cache():
    async def run():
        fetch = AsyncMock(return_value=snapshot()); clock = [0]
        service = FrontDecks(fetch, clock=lambda: clock[0])
        rows = await service.top()
        assert len(rows) == 20 and [r.rank for r in rows] == list(range(1,21))
        assert [r.player for r in rows] == snapshot()['ranking_order'][:20]
        assert rows[4].deck_name is None and rows[5].deck_name == ''
        for row in rows:
            url = urlsplit(row.url)
            assert url.netloc == 'ygo.xyk.one' and url.path == '/mc/player/'
            assert parse_qs(url.query)['name'] == [row.player]
        assert await service.top() is rows
        fetch.assert_awaited_once()
        clock[0] = 31
        await service.top()
        assert fetch.await_count == 2
        return rows
    rows = asyncio.run(run())
    text = render_front_decks(rows)
    assert text.mode == 'markdown'
    assert '1. [灵sama【狱神奥义耀圣】](' in text
    assert '5. [玩家5【暂无卡组】]' in text and '6. [玩家6【未识别】]' in text
    assert text.count('](https://ygo.xyk.one/mc/player/?name=') == 20
    assert '](https://evil.example)' not in text
    assert text.endswith('> 更多内容请点击链接[进入网页版](https://ygo.xyk.one/mc)查看！')
    spec = default(ROOT); spec['mode'] = 'text'
    with draft(ROOT, spec):
        plain = render_front_decks(rows)
        assert plain.startswith('1. 灵sama【狱神奥义耀圣】\nhttps://') and '](' not in plain.splitlines()[0]
        assert plain.count('https://ygo.xyk.one/mc/player/?name=') == 20


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'short'])
def test_inconsistent_snapshot_never_invents_or_reorders_players(change):
    data = snapshot()
    if change == 'missing': data['players'].pop()
    if change == 'duplicate': data['ranking_order'][0] = data['ranking_order'][1]
    if change == 'short': data['ranking_order'] = data['ranking_order'][:19]
    with pytest.raises(ValueError):
        asyncio.run(FrontDecks(AsyncMock(return_value=data)).top())


def test_old_default_upgrades_and_custom_copy_is_preserved():
    old = default(ROOT); old.update(template='请查看[网页版](https://ygo.xyk.one/mc)', visible=[])
    new = upgrade_spec(ROOT, old)
    assert new['template'] == '{rows}\n\n{footer}' and set(new['visible']) == {'rows','footer'}
    old['template'] = '我的说明'
    assert upgrade_spec(ROOT, old) == old


def test_actual_matcher_in_group_no_at_group_at_and_private():
    script = r'''
import asyncio,sys
from unittest.mock import AsyncMock
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove();nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.mycard_front import FrontDecks
from test_front_decks_reply import snapshot
from test_qq_alias_choices import source,messages
from test_qq_art_markdown import gallery_transport
async def run():
 for kind in ['c2c','group','group-no-at']:
  service=FrontDecks(AsyncMock(return_value=snapshot()))
  config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user'}),accept_joined_groups=True)
  handler=register(config,queries=Queries(None,front_decks=service)).handlers[0].call
  client=gallery_transport();event=source(kind);event.content='前排卡组信息'
  await handler(client,event)
  payloads=[m.json for m in messages(client)]
  assert len(payloads)==1 and payloads[0]['msg_type']==2
  content=''.join(p['markdown']['content'] for p in payloads)
  assert len(content)>1500
  assert content.count('](https://ygo.xyk.one/mc/player/?name=')==20
  assert content.count('[进入网页版](https://ygo.xyk.one/mc)')==1
  assert 'keyboard' not in payloads[0]
  service.cached=None;service.fetch=AsyncMock(side_effect=TimeoutError())
  event=source(kind);event.id='failure';event.content='前排卡组信息'
  await handler(client,event)
  assert '暂时无法查询' in messages(client)[-1].json['markdown']['content']
asyncio.run(run())
'''
    result = subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stderr[-5000:]+result.stdout[-2000:]
