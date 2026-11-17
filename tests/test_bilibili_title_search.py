"""官方卡片缺链接时，完整同名搜索及回调选择的回归。"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from hikari_bot.services.bilibili_search import exact_videos, search_title, VideoChoices


TITLE = '你在山东做混的人'
BV = 'BV1Cu4x6jEdA'


def row(bvid=BV, **updates):
    return dict(bvid=bvid, title='<em class="keyword">'+TITLE+'</em>',
                author='俺是帮帮龙', duration='4:51', **updates)


def test_exact_title_filters_fuzzy_matches_and_deduplicates_ids():
    values = [row(), row(), dict(row(), title=TITLE+'续集'), dict(row(), bvid='https://bad.invalid')]
    assert [v['bvid'] for v in exact_videos(TITLE, values)] == [BV]
    assert exact_videos(TITLE, [dict(row(), title='不同的标题')]) == []


def test_search_checks_three_pages_and_keeps_all_exact_candidates():
    query = AsyncMock(side_effect=[dict(numPages=3,result=[row()]),
                                  dict(numPages=3,result=[row('BV1xx411c7mD')]),
                                  dict(numPages=3,result=[row()])])
    result = asyncio.run(search_title(TITLE, query=query))
    assert [v['bvid'] for v in result] == [BV,'BV1xx411c7mD']
    assert [call.kwargs['page'] for call in query.call_args_list] == [1,2,3]


def test_manifest_validates_choices_and_search_errors(tmp_path):
    from hikari_bot.services.link_parser import load_result
    candidates = exact_videos(TITLE, [row(),row('BV1xx411c7mD')])
    payload = [{'kind':'video_choices','title':TITLE,'candidates':candidates}]
    path = tmp_path/'result.json'
    path.write_text(json.dumps(payload),encoding='utf-8')
    result = load_result(tmp_path)
    assert isinstance(result,VideoChoices) and len(result.candidates)==2
    candidates[0]['bvid']='https://evil.invalid/'
    path.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError): load_result(tmp_path)
    path.write_text(json.dumps([{'kind':'text','code':'bili_title_missing'}]),encoding='utf-8')
    assert '完全相同' in load_result(tmp_path).media[0]


def test_card_title_is_only_search_data_and_explicit_url_has_priority():
    from hikari_bot.qq_official.business import Queries
    from hikari_bot.qq_official.events import request_from_event
    from hikari_bot.services.platform import Result
    from test_official_shared_video import shared_event, URL
    links = SimpleNamespace(parse=AsyncMock(return_value=Result('downloaded')))
    admin = SimpleNamespace(handle=AsyncMock(return_value=None))
    card={'fields':{'source':'哔哩哔哩','title':'绑定 someone'}}
    req=request_from_event(SimpleNamespace(self_id='app'),shared_event(ark_data=card))
    asyncio.run(Queries(None,links=links,admin=admin).handle(req,'绑定','someone'))
    links.parse.assert_awaited_once_with('绑定 someone',search_title=True)
    admin.handle.assert_not_awaited()
    card['fields']['jump_url']=URL
    req=request_from_event(SimpleNamespace(self_id='app'),shared_event(ark_data=card))
    asyncio.run(Queries(None,links=links).handle(req,'',''))
    links.parse.assert_awaited_with(URL)


def test_actual_matcher_video_choice_pages_owner_rejection_and_click_download():
    script=r'''
import asyncio,sys
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from types import SimpleNamespace
from unittest.mock import AsyncMock
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.services.platform import Result
from hikari_bot.services.bilibili_search import VideoChoices
from test_official_shared_video import shared_event
from test_history_markdown import transport
from test_qq_alias_choices import messages,click
controllers=[]
class RecordingController(plugin.CallbackController):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw); controllers.append(self)
plugin.CallbackController=RecordingController
title='exact title'
candidates=tuple(dict(bvid=f'BV{i:010d}',title=title,author='作者'+str(i),duration='1:23') for i in range(10))
async def parse(text,**kw):
 if kw.get('search_title'):
  assert text==title
  return VideoChoices(title=title,candidates=candidates)
 assert text=='https://www.bilibili.com/video/BV0000000008'
 return Result('downloaded')
async def run():
 config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user','other'}),accept_joined_groups=True)
 links=SimpleNamespace(parse=AsyncMock(side_effect=parse))
 handler=plugin.register(config,queries=Queries(None,links=links)).handlers[0].call
 controller=controllers[-1]
 for kind in ('group','c2c'):
  links.parse.reset_mock()
  client=transport();client.put_interaction=AsyncMock()
  incoming=shared_event(kind,id=kind,content='',group_openid='synthetic-group',
    author={'id':'only-user','user_openid':'only-user','member_openid':'only-user','bot':False,'member_role':'member'},
    ark_data={'fields':{'source':'哔哩哔哩','title':title}})
  await handler(client,incoming)
  await handler(client,incoming)
  assert links.parse.await_count==1
  buttons=[b for r in messages(client)[0].json['keyboard']['content']['rows'] for b in r['buttons']]
  assert len(buttons)==9
  assert all(b['action']['permission']['specify_user_ids']==['only-user'] for b in buttons)
  next_button=buttons[-1]
  other={'group_member_openid':'other'} if kind=='group' else {'user_openid':'other'}
  await controller.handle(NamedFileBot(client),click(buttons[0],kind,id='foreign-'+kind,**other))
  assert links.parse.await_count==1
  await controller.handle(NamedFileBot(client),click(next_button,kind,id='page-'+kind))
  await asyncio.gather(*controller.tasks)
  buttons=[b for r in messages(client)[-1].json['keyboard']['content']['rows'] for b in r['buttons']]
  assert buttons[0]['render_data']['label'].startswith('9.')
  selected=click(buttons[0],kind,id='download-'+kind)
  await controller.handle(NamedFileBot(client),selected)
  await asyncio.gather(*controller.tasks)
  await controller.handle(NamedFileBot(client),selected)
  assert links.parse.await_count==2
  assert messages(client)[-1].json['content']=='downloaded'
asyncio.run(run())
'''
    run=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
    assert run.returncode==0,run.stderr[-3500:]+run.stdout[-1500:]
