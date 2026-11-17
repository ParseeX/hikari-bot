"""正文指令链接只填入固定示例，普通文字不泄漏客户端协议。"""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from hikari_bot.qq_official import output_templates as templates
from hikari_bot.qq_official.output_templates import default, draft, present
from hikari_bot.qq_official.prices import PriceQueries


@pytest.mark.parametrize('key', ['effect.card', 'art.gallery', 'art.page', 'common.passcode',
                                     'price.prompt', 'calculation.business_body_82946755'])
def test_notice_is_editable_command_link_only_in_markdown(key):
    for mode in ['text', 'markdown']:
        spec = default(key)
        spec['mode'] = mode
        with draft(key, spec):
            output = present(key, {'name': '任意卡名', 'images': '图片位置'})
        assert output.mode == mode
        assert output.count('卡名识别有误？') == 1
        if mode == 'text':
            assert 'mqqapi:' not in output
            assert '可使用 添加别名 卡名 别名 指令' in output
            assert '[添加别名' not in output
        else:
            target = re.search(r'\[添加别名 卡名 别名\]\(([^)]+)\)', output)[1]
            url = urlsplit(target)
            assert (url.scheme, url.netloc, url.path) == ('mqqapi', 'aio', '/inlinecmd')
            assert parse_qs(url.query) == {'command': ['添加别名'],
                                         'enter': ['false'], 'reply': ['false']}
            assert '任意卡名' not in target and ' ' not in target


def test_multiple_price_blocks_have_no_alias_hint(monkeypatch):
    import hikari_bot.qq_official.prices as prices
    async def run():
        backend = PriceQueries(None, None, None, clock=lambda: 0)
        backend.sessions['u'] = (100, 'card', {'UR': []}, False)
        backend.comparison = SimpleNamespace(compare=AsyncMock(return_value=[]))
        monkeypatch.setattr(prices, 'format_comparison', lambda *a, **kw: [
            present('price.result', {'heading': '第一块', 'versions': '价格一'}),
            present('price.result', {'heading': '第二块', 'versions': '价格二'})])
        spec = default('price.result'); spec['mode'] = 'markdown'
        with draft('price.result', spec):
            result = await backend.choose('UR', 'u')
        assert '价格一' in result and '价格二' in result
        assert result.count('mqqapi://aio/inlinecmd') == 0
        assert result.mode == 'markdown'
    asyncio.run(run())


def test_editor_preview_matches_inline_link_but_custom_notice_is_preserved(tmp_path, monkeypatch):
    from hikari_bot.qq_official.editor_examples import render_example
    monkeypatch.setattr(templates, 'HOME', tmp_path)
    result = render_example(templates.Store(tmp_path), 'effect.card', default('effect.card'))
    assert 'mqqapi://aio/inlinecmd' in result['context']
    spec = default('common.card_name_hint'); spec['template'] = '> 我的自定义提示'
    with draft('common.card_name_hint', spec):
        assert present('effect.card').endswith('> 我的自定义提示')


def test_button_notice_context_is_task_local_and_plain_fragment_is_suppressed():
    from hikari_bot.qq_official.output_templates import CARD_NAME_CONFIRMED
    async def run():
        async def clicked():
            token = CARD_NAME_CONFIRMED.set(True)
            try:
                await asyncio.sleep(0)
                assert present('common.card_name_hint', plain=True) == ''
                assert '卡名识别有误？' not in present('effect.card')
            finally:
                CARD_NAME_CONFIRMED.reset(token)
        async def manual():
            await asyncio.sleep(0)
            assert '卡名识别有误？' in present('effect.card')
        await asyncio.gather(clicked(), manual())
        assert '卡名识别有误？' in present('effect.card')
    asyncio.run(run())


def test_existing_gallery_template_gains_buttons_without_changing_copy():
    from hikari_bot.qq_official.output_templates import upgrade_spec, validate
    spec = default('art.gallery')
    spec.update(template='我的卡图\n{images}', rows=[], labels={})
    upgraded = validate('art.gallery', upgrade_spec('art.gallery', spec))
    assert upgraded['template'] == spec['template']
    assert upgraded['rows'] == [['effect', 'price']]
    assert upgraded['labels'] == {'effect': '查看效果', 'price': '查询卡价'}
