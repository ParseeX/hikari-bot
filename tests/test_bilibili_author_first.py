"""有明确 UP 主时，用户定位和投稿查询必须先于全站视频搜索。"""
import asyncio
from unittest.mock import AsyncMock
import pytest
from bilibili_api import search
from hikari_bot.services.bilibili_search import search_title
from test_bilibili_author_fallback import TITLE,OWNER,UID,TARGET,RELATED,archive


def test_known_author_checks_uploads_without_global_video_search():
    calls=[]
    async def query(**kw):
        calls.append(kw['search_type'])
        assert kw['search_type'] == search.SearchObjectType.USER
        assert kw['keyword'] == OWNER
        return dict(result=[dict(mid=UID,uname=OWNER)])
    async def uploads(uid,**kw):
        calls.append('uploads')
        assert uid == UID
        return archive([dict(bvid=TARGET,title=TITLE,author=OWNER,mid=UID)])
    found=asyncio.run(search_title(TITLE,author=OWNER,query=query,author_query=uploads))
    assert [r['bvid'] for r in found] == [TARGET]
    assert calls == [search.SearchObjectType.USER,'uploads']


@pytest.mark.parametrize('users', [[],[dict(mid=UID,uname=OWNER+'搬运')],
    [dict(mid=UID,uname=OWNER),dict(mid=123,uname=OWNER)],
    [dict(mid=True,uname=OWNER)]])
def test_unconfirmed_author_falls_back_to_global_search(users):
    calls=[]
    async def query(**kw):
        calls.append(kw['search_type'])
        if kw['search_type'] == search.SearchObjectType.USER:return dict(result=users)
        return dict(numPages=1,result=[dict(bvid=TARGET,title=TITLE,author=OWNER)])
    uploads=AsyncMock()
    assert asyncio.run(search_title(TITLE,author=OWNER,query=query,author_query=uploads))[0]['bvid']==TARGET
    assert calls == [search.SearchObjectType.USER,search.SearchObjectType.VIDEO]
    uploads.assert_not_awaited()


def test_empty_uploads_then_global_search_without_repeating_same_archive():
    calls=[]
    async def query(**kw):
        calls.append(kw['search_type'])
        if kw['search_type'] == search.SearchObjectType.USER:return dict(result=[dict(mid=str(UID),uname=OWNER)])
        return dict(numPages=1,result=[RELATED])
    async def uploads(*a,**kw):
        calls.append('uploads')
        return archive([])
    assert asyncio.run(search_title(TITLE,author=OWNER,query=query,author_query=uploads))==[]
    assert calls[:3]==[search.SearchObjectType.USER,'uploads',search.SearchObjectType.VIDEO]
    assert calls.count('uploads')==1


def test_no_author_keeps_global_search_first():
    query=AsyncMock(return_value=dict(numPages=1,result=[dict(bvid=TARGET,title=TITLE,author=OWNER)]))
    uploads=AsyncMock()
    assert asyncio.run(search_title(TITLE,query=query,author_query=uploads))[0]['bvid']==TARGET
    assert query.call_args.kwargs['search_type']==search.SearchObjectType.VIDEO
    uploads.assert_not_awaited()


def test_author_lookup_failure_is_not_silently_treated_as_no_author():
    query=AsyncMock(side_effect=RuntimeError('lookup unavailable'))
    with pytest.raises(RuntimeError):asyncio.run(search_title(TITLE,author=OWNER,query=query))
    assert query.call_args.kwargs['search_type']==search.SearchObjectType.USER
