"""全站搜索漏掉目标视频时，通过相关作者投稿定位，不能下载近似标题。"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from hikari_bot.services.bilibili_search import search_title

TITLE = '【召唤兽】MC天梯竞技实录【附文字解说】'
OWNER = 'Cr我的船长'
UID = 402125945
TARGET = 'BV1UbYP6yEW4'
RELATED = dict(bvid='BV1XDJi6kEE2',title='【召唤兽】十月表MC天梯竞技实录【附文字解说】',
               author=OWNER,mid=UID,duration='2:00')


def archive(rows, count=None):
    return dict(list=dict(vlist=rows),page=dict(count=len(rows) if count is None else count))


@pytest.mark.parametrize('author', ['', OWNER])
def test_screenshot_video_missing_from_search_is_found_in_author_uploads(author):
    query = AsyncMock(return_value=dict(numPages=1,result=[RELATED]))
    uploads = AsyncMock(return_value=archive([RELATED,dict(bvid=TARGET,title=TITLE,author=OWNER,
                                                        mid=UID,length='9:12')]))
    found = asyncio.run(search_title(TITLE,author=author,query=query,author_query=uploads))
    assert [r['bvid'] for r in found] == [TARGET]
    assert found[0]['duration'] == '9:12'
    uploads.assert_awaited_once_with(UID,pn=1,ps=50)


def test_similar_titles_in_author_uploads_are_not_auto_downloaded():
    query = AsyncMock(return_value=dict(numPages=1,result=[RELATED]))
    uploads = AsyncMock(return_value=archive([RELATED]))
    assert asyncio.run(search_title(TITLE,query=query,author_query=uploads)) == []


def test_wrong_author_or_mid_from_uploads_is_rejected():
    query = AsyncMock(return_value=dict(numPages=1,result=[RELATED]))
    uploads = AsyncMock(return_value=archive([
        dict(bvid=TARGET,title=TITLE,author='其他作者',mid=UID),
        dict(bvid=TARGET,title=TITLE,author=OWNER,mid=123)]))
    assert asyncio.run(search_title(TITLE,author=OWNER,query=query,author_query=uploads)) == []


def test_next_author_page_keeps_duplicate_title_candidates():
    query = AsyncMock(return_value=dict(numPages=1,result=[RELATED]))
    first = [dict(bvid=TARGET,title=TITLE,author=OWNER,mid=UID)] + [RELATED]*49
    second = [dict(bvid='BV1xx411c7mD',title=TITLE,author=OWNER,mid=UID)]
    uploads = AsyncMock(side_effect=[archive(first,51),archive(second,51)])
    found = asyncio.run(search_title(TITLE,query=query,author_query=uploads))
    assert [r['bvid'] for r in found] == [TARGET,'BV1xx411c7mD']
    assert [c.kwargs['pn'] for c in uploads.await_args_list] == [1,2]


def test_irrelevant_global_results_and_invalid_uids_do_not_trigger_scanning():
    query = AsyncMock(return_value=dict(numPages=1,result=[
        {**RELATED,'mid':'https://evil.invalid'},
        {**RELATED,'mid':42,'title':'完全无关的视频'},
        {**RELATED,'mid':True}]))
    uploads = AsyncMock()
    assert asyncio.run(search_title(TITLE,query=query,author_query=uploads)) == []
    uploads.assert_not_awaited()


def test_existing_exact_global_match_does_not_scan_uploads():
    query = AsyncMock(return_value=dict(numPages=1,result=[{**RELATED,'title':TITLE,'bvid':TARGET}]))
    uploads = AsyncMock()
    found = asyncio.run(search_title(TITLE,query=query,author_query=uploads))
    assert found[0]['bvid'] == TARGET
    uploads.assert_not_awaited()



def test_upload_failure_is_not_reported_as_no_matches():
    query = AsyncMock(return_value=dict(numPages=1,result=[RELATED]))
    uploads = AsyncMock(side_effect=RuntimeError('upstream unavailable'))
    with pytest.raises(RuntimeError):
        asyncio.run(search_title(TITLE,query=query,author_query=uploads))


def test_author_scan_is_bounded_to_three_accounts_two_pages_each():
    related = [{**RELATED,'mid':n,'author':f'作者{n}'} for n in range(1,8)]
    query = AsyncMock(return_value=dict(numPages=1,result=related))
    uploads = AsyncMock(return_value=archive([RELATED]*50,500))
    assert asyncio.run(search_title(TITLE,query=query,author_query=uploads)) == []
    assert uploads.await_count == 6
    assert {c.args[0] for c in uploads.await_args_list} == {1,2,3}


def test_card_layout_spaces_next_to_chinese_brackets_do_not_hide_match():
    from hikari_bot.services.bilibili_search import exact_videos
    assert exact_videos('【召唤兽】 MC天梯竞技实录 【附文字解说】',
                        [dict(bvid=TARGET,title=TITLE,author=OWNER)])
