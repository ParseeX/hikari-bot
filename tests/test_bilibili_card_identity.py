"""B 站分享卡片保留标题符号，并优先使用原视频身份与作者。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from hikari_bot.services.bilibili_search import exact_videos, search_title
from hikari_bot.services.platform import Result
from hikari_bot.qq_official.events import request_from_event
from hikari_bot.qq_official.business import Queries
from test_official_shared_video import shared_event

BV = 'BV1xx411c7mD'
URL = 'https://www.bilibili.com/video/' + BV


@pytest.mark.parametrize('title,result', [
    ('A &amp; B', 'A &amp; B'), ('这首 <Live> 好听', '这首 <Live> 好听'),
    ('Ａ＆Ｂ　现场', '<em class="keyword">A&amp;B</em> 现场'),
    ('“现场”好听', '&quot;现场&quot;好听'),
])
def test_special_characters_do_not_hide_same_title(title, result):
    assert exact_videos(title, [dict(bvid=BV,title=result,author='UP')])[0]['bvid'] == BV


@pytest.mark.parametrize('extra', [
    {'bvid': BV}, {'extra': {'bvid': BV}},
    {'url': 'https%3A%2F%2Fwww.bilibili.com%2Fvideo%2F' + BV},
    {'path': 'pages/video/video?bvid=' + BV},
    {'raw': '{"meta":{"detail_1":{"bvid":"' + BV + '"}}}'},
])
def test_card_bv_and_encoded_links_have_priority_over_search(extra):
    event = shared_event(ark_data={'fields': {'source':'哔哩哔哩','title':'同名视频',**extra}})
    request = request_from_event(SimpleNamespace(self_id='app'), event)
    assert request.shared_video == URL
    links = SimpleNamespace(parse=AsyncMock(return_value=Result()))
    asyncio.run(Queries(None,links=links).handle(request,'',''))
    links.parse.assert_awaited_once_with(URL)


@pytest.mark.parametrize('kind', ['group', 'group_at', 'c2c'])
def test_card_author_reaches_search_and_is_not_the_qq_sender(kind):
    event = shared_event(kind, ark_data={'fields': {'source':'哔哩哔哩','title':'视频',
                        'author': {'name':'实际UP'}}}, author={'id':'u','user_openid':'u',
                        'member_openid':'u','bot':False,'member_role':'member','username':'发送者'})
    request = request_from_event(SimpleNamespace(self_id='app'), event)
    links = SimpleNamespace(parse=AsyncMock(return_value=Result()))
    asyncio.run(Queries(None,links=links).handle(request,'',''))
    links.parse.assert_awaited_once_with('视频',search_title=True,author='实际UP')


def test_search_filters_same_title_by_author_and_preserves_multiple_matches():
    query = AsyncMock(return_value=dict(numPages=1,result=[
        dict(bvid=BV,title='A &amp; B',author='实际UP'),
        dict(bvid='BV1Cu4x6jEdA',title='A &amp; B',author='搬运者')]))
    result = asyncio.run(search_title('A &amp; B',author='实际UP',query=query))
    assert [v['bvid'] for v in result] == [BV]
    assert query.call_args.kwargs['keyword'] == '实际UP A & B'


def test_symbol_free_search_fallback_does_not_relax_final_title_match():
    query = AsyncMock(side_effect=[dict(numPages=1,result=[]),dict(numPages=1,result=[
        dict(bvid=BV,title='【现场】 A &amp; B ♪',author='实际UP'),
        dict(bvid='BV1Cu4x6jEdA',title='现场 A B',author='实际UP')])])
    result = asyncio.run(search_title('【现场】 A & B ♪',author='实际UP',query=query,
                                      user_query=AsyncMock(return_value={'result':[]})))
    assert [v['bvid'] for v in result] == [BV]
    assert query.call_args_list[1].kwargs['keyword'] == '实际UP 现场 A B'


@pytest.mark.parametrize('field', ['preview', 'title', 'desc'])
def test_bv_is_not_guessed_from_cover_or_caption(field):
    event = shared_event(ark_data={'fields': {'source':'哔哩哔哩', field:URL}})
    req = request_from_event(SimpleNamespace(self_id='app'), event)
    assert req.shared_video == ''


def test_author_does_not_fall_back_to_sender_or_another_forwarded_card():
    from hikari_bot.qq_official.shared_video import shared_author
    event = dict(message_type=103, author={'name':'sender'}, msg_elements=[
        dict(ark_data={'fields':{'source':'哔哩哔哩','title':'first'}}),
        dict(ark_data={'fields':{'source':'哔哩哔哩','title':'second','author':'second-up'}})])
    assert shared_author(event) == ''
    unrelated = shared_event(ark_data={'fields':{'source':'其他应用','bvid':BV,'author':'not-up'}},
                            _hikari_shared_video_author='forged')
    req = request_from_event(SimpleNamespace(self_id='app'), unrelated)
    assert req.shared_video is None and req.shared_video_author == ''


def test_same_author_same_title_keeps_all_candidates_and_rejects_other_owner(tmp_path):
    from hikari_bot.services.link_parser import load_result
    import json
    candidates = [dict(bvid=BV,title='标题',author='作者'),
                  dict(bvid='BV1Cu4x6jEdA',title='标题',author='作者')]
    result = exact_videos('标题', candidates, author='作者')
    assert len(result) == 2
    data = [dict(kind='video_choices',title='标题',author='作者',candidates=result)]
    target = tmp_path/'result.json'
    target.write_text(json.dumps(data),encoding='utf-8')
    assert len(load_result(tmp_path).candidates) == 2
    data[0]['candidates'][1]['author'] = 'other'
    target.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError): load_result(tmp_path)


def test_no_symbols_or_wrong_author_never_becomes_a_fuzzy_match():
    assert exact_videos('A+B',[dict(bvid=BV,title='AB',author='作者')]) == []
    query = AsyncMock(return_value=dict(numPages=1,result=[dict(bvid=BV,title='A+B',author='搬运者')]))
    assert asyncio.run(search_title('A+B',author='原作者',query=query,
                                   user_query=AsyncMock(return_value={'result':[]}))) == []
    assert query.await_count == 2
