"""转发视频在真实官方事件入口的回归；不向 QQ 发送消息。"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import subprocess
import sys

import pytest

from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.events import request_from_event
from hikari_bot.services.platform import Result


URL = 'https://www.bilibili.com/video/BV1xx411c7mD?p=2'


def shared_event(kind='group', **extra):
    data = dict(id='synthetic-event', content='[分享] https://m.q.qq.com/a/s/synthetic',
                timestamp=datetime.now(timezone.utc).isoformat(),
                author=dict(id='u', user_openid='u', member_openid='u', bot=False, member_role='member'),
                group_id='g', group_openid='g', message_type=3,
                ark_data=dict(ark_type='miniapp', fields=dict(tag='哔哩哔哩', jump_url=URL)))
    data.update(extra)
    from nonebot.adapters.qq.models import Dispatch
    from hikari_bot.qq_official.gateway import SrbsQQAdapter
    payload = Dispatch.model_validate(dict(op=0, s=1,
        t={'group': 'GROUP_MESSAGE_CREATE', 'group_at': 'GROUP_AT_MESSAGE_CREATE',
           'c2c': 'C2C_MESSAGE_CREATE'}[kind], d=data))
    return SrbsQQAdapter.payload_to_event(payload)


@pytest.mark.parametrize('kind', ['group', 'group_at', 'c2c'])
def test_card_passes_video_address_instead_of_qq_wrapper_to_parser(kind):
    links = SimpleNamespace(parse=AsyncMock(return_value=Result('video result')))
    request = request_from_event(SimpleNamespace(self_id='app'), shared_event(kind))
    asyncio.run(Queries(None, links=links).handle(request, '', ''))
    links.parse.assert_awaited_once_with(URL)


@pytest.mark.parametrize('kind', ['group', 'c2c'])
def test_forwarded_elements_and_short_link_escape(kind):
    card = dict(message_type=3, msg_idx='2', content='', ark_data=dict(
        ark_type='miniapp', fields=dict(jump_url=r'https:\/\/b23.tv\/example?x=1&amp;y=2')))
    event = shared_event(kind, content='', ark_data=None, message_type=103,
                         msg_elements=[dict(message_type=102, msg_idx='1', content='', msg_elements=[card])])
    request = request_from_event(SimpleNamespace(self_id='app'), event)
    assert request.shared_video == 'https://b23.tv/example?x=1&y=2'


@pytest.mark.parametrize('url', [None, 'https://m.q.qq.com/a/s/example',
    'https://b23.tv.evil.invalid/a', 'https://b23.tv@evil.invalid/a',
    'https://b23.tv:123/a', 'https://bilibili.com/irrelevant', 'https://b23.tv:bad/a'])
def test_card_without_usable_url_explains_copy_link_and_never_searches_title(url):
    event = shared_event(ark_data=dict(ark_type='miniapp', fields=dict(
        tag='哔哩哔哩', title='绑定 some-player', preview=URL, jump_url=url)))
    links = SimpleNamespace(parse=AsyncMock())
    state = SimpleNamespace(bind=AsyncMock())
    request = request_from_event(SimpleNamespace(self_id='app'), event)
    result = asyncio.run(Queries(None, links=links, state=state).handle(request, '绑定', 'some-player'))
    assert '复制链接' in result.text
    links.parse.assert_not_awaited()
    state.bind.assert_not_awaited()


def test_plain_text_unrelated_cards_quotes_and_limits_are_unchanged():
    from hikari_bot.qq_official.shared_video import shared_video
    assert shared_video(dict(content=URL, message_type=0)) is None
    assert shared_video(dict(content='ping', message_type=101, ark_data={}, msg_elements=[dict(content=URL)])) is None
    assert shared_video(dict(message_type=3, ark_data=dict(fields=dict(tag='其他应用', preview=URL)))) is None
    assert shared_video(dict(message_type=103, msg_elements=[dict(content=URL)])) == URL
    node = dict(content=URL)
    for _ in range(6):
        node = dict(msg_elements=[node], message_type=103)
    assert shared_video(node) is None
    assert shared_video(dict(message_type=103, msg_elements=[dict(content='')] * 80 + [dict(content=URL)])) is None


def test_official_flattened_forward_example_and_forged_extra_field():
    event = shared_event(message_type=103, ark_data=None, content='',
                         msg_elements=[dict(content='=== 消息 1 ===\n' + URL)])
    assert request_from_event(SimpleNamespace(self_id='app'), event).shared_video == URL
    event = shared_event(message_type=0, ark_data=None, content='ping', _hikari_shared_video=URL)
    assert request_from_event(SimpleNamespace(self_id='app'), event).shared_video is None


@pytest.mark.parametrize('section,key', [('detail_1', 'qqdocurl'), ('news', 'jumpUrl'), ('music', 'jumpUrl')])
@pytest.mark.parametrize('container', ['meta', 'raw', 'field_raw'])
def test_legacy_parser_card_fields_reach_same_downloader(section, key, container):
    original = {'meta': {section: {key: URL}}}
    card = {'ark_type': 'miniapp', 'fields': {'tag': '哔哩哔哩'}}
    if container == 'meta':
        card.update(original)
    elif container == 'raw':
        card['raw'] = json.dumps(original)
    else:
        card['fields']['raw'] = json.dumps(original)
    links = SimpleNamespace(parse=AsyncMock(return_value=Result('video result')))
    req = request_from_event(SimpleNamespace(self_id='app'), shared_event(ark_data=card))
    asyncio.run(Queries(None, links=links).handle(req, '', ''))
    links.parse.assert_awaited_once_with(URL)


def test_legacy_card_invalid_json_and_unrelated_link_do_not_guess():
    from hikari_bot.qq_official.shared_video import legacy_video
    for raw in ('{bad', '[' * 1200, 'x' * 16001,
                json.dumps({'meta': {'detail_1': {'qqdocurl': 'https://evil.invalid/'}}})):
        assert legacy_video({'raw': raw}) is None
    assert legacy_video({'title': URL, 'fields': {'preview': URL}}) is None


def test_probe_is_opt_in_bounded_and_never_records_payload_values(tmp_path, monkeypatch, capsys):
    from hikari_bot.qq_official import share_probe
    marker = tmp_path / 'until'
    monkeypatch.setattr(share_probe, 'MARKER', marker)
    monkeypatch.setattr(share_probe, '_samples', 0)
    monkeypatch.setattr(share_probe.time, 'time', lambda: 1000)
    event = {'author': {'id': 'private-user'}, 'message_scene': {'auth_token': 'private-token'},
             'content': 'private-message', 'ark_data': {'fields': {
                 'title': 'private-title', 'preview': 'https://other.invalid/private-path?key=private-key',
                 'private-dynamic-key': 'private-value', 'raw': json.dumps({'meta': {
                     'detail_1': {'qqdocurl': URL}}})}}}
    share_probe.capture_missing_card(event)
    assert capsys.readouterr().out == ''
    marker.write_text('1500')
    for _ in range(5):
        share_probe.capture_missing_card(event)
    output = capsys.readouterr().out
    assert output.count('SRBS_SHARE_SHAPE') == 3
    assert 'private' not in output and URL not in output and 'BV1' not in output
    assert 'qqdocurl' in output and '"video_url": true' in output
    marker.write_text('999')
    monkeypatch.setattr(share_probe, '_samples', 0)
    share_probe.capture_missing_card(event)
    assert capsys.readouterr().out == ''


def test_parser_switch_is_respected_for_shared_cards():
    links = SimpleNamespace(parse=AsyncMock())
    notifications = SimpleNamespace(bus=SimpleNamespace(setting=lambda *args: False))
    request = request_from_event(SimpleNamespace(self_id='app'), shared_event())
    result = asyncio.run(Queries(None, links=links, notifications=notifications).handle(request, '', ''))
    assert '关闭' in result.text
    links.parse.assert_not_awaited()


def test_real_matcher_handles_non_at_cards_once_and_does_not_execute_titles():
    script = r'''
import asyncio, sys
sys.path.insert(0, 'tests')
import nonebot
nonebot.logger.remove(); nonebot.init(driver='~aiohttp', _env_file=[], log_level='ERROR')
from unittest.mock import AsyncMock
from types import SimpleNamespace
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.business import Queries
from hikari_bot.services.platform import Result
from test_official_shared_video import shared_event, URL
from test_history_markdown import transport
from test_qq_alias_choices import messages
async def run():
    links=SimpleNamespace(parse=AsyncMock(return_value=Result('parsed video')))
    admin=SimpleNamespace(handle=AsyncMock(return_value=None), management=None)
    config=PilotConfig('synthetic-app','fake',users=frozenset({'u'}),accept_joined_groups=True)
    handler=register(config,queries=Queries(None,links=links,admin=admin)).handlers[0].call
    for kind in ('group','group_at','c2c'):
        client=transport()
        event=shared_event(kind, id=kind, content='绑定 someone')
        await handler(client,event)
        await handler(client,event)
        assert len(messages(client)) == 1
        assert messages(client)[0].json['content']=='parsed video'
        links.parse.assert_awaited_with(URL)
    assert links.parse.await_count==3
    admin.handle.assert_not_awaited()
    client=transport(); client.self_id='other-app'
    await handler(client,shared_event(id='foreign'))
    assert not messages(client)
    client=transport()
    event=shared_event(id='missing',content='',ark_data={'fields':{'tag':'哔哩哔哩'}})
    await handler(client,event)
    assert '复制链接' in messages(client)[0].json['content']
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-1500:]
