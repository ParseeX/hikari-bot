"""报价 Markdown 的层次、币种、缺失数据与普通文字切换。"""
import asyncio
from decimal import Decimal

import pytest

from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import Comparison
from hikari_bot.features.cardrush.models import PriceSnapshot
from hikari_bot.qq_official.price_outputs import format_comparison, rarity_prompt
from hikari_bot.qq_official.output_templates import default, draft, present, upgrade_spec
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.media import NamedFileBot
from test_history_markdown import transport
from test_qq_alias_choices import source, messages
from test_qq_preview import NOW


def prices():
    return [Comparison(CardVersion(1,'TEST-JP001','UR'),JhsPrice(1,Decimal('12.50'),Decimal('13.20'),None),
                       (PriceSnapshot(1,'card','UR','TEST-JP001',1500,'2026-11-18'),)),
            Comparison(CardVersion(2,'TEST-JP002','UR'),None,(),True),
            Comparison(CardVersion(3,'TEST-JP003','UR'),JhsPrice(3,None,None,None),())]


@pytest.mark.parametrize('kind',['c2c','group','group-no-at'])
def test_result_sends_markdown_with_all_versions_and_explicit_missing_prices(kind):
    async def run():
        result=format_comparison('card', 'UR', prices())[0]
        assert result.mode=='markdown' and result.startswith('## card | UR\n\n### TEST-JP001')
        assert result.count('### TEST-JP')==3
        assert '- 集换社最低价：**12.50 元**' in result
        assert '- Cardrush 买取价：**1,500 円**' in result
        assert '集换社：暂时不可用' in result and 'Cardrush 买取价：暂时不可用' in result
        assert '暂无数据' in result and '数据库暂无对应记录' in result
        assert '添加别名' not in result
        client=transport()
        await ReplyPager(now=lambda:NOW).send(NamedFileBot(client),source(kind),result)
        payload=messages(client)[0].json
        assert payload['msg_type']==2 and payload['markdown']['content']==result
    asyncio.run(run())


def test_plain_text_defaults_have_no_markdown_and_custom_copy_is_not_stripped():
    spec=default('price.result');spec['mode']='text'
    with draft('price.result',spec):
        result=format_comparison('card','UR',prices())[0]
        assert result.startswith('【card | UR】\nTEST-JP001')
        assert '**' not in result and '###' not in result and '> ' not in result
        assert '集换社最低价：12.50 元' in result
    spec=default('price.jhs');spec['template']='我的报价 **{minimum}**'
    with draft('price.jhs',spec):
        assert present('price.jhs',{'minimum':'12 元'},plain=True)=='我的报价 **12 元**'


def test_prompt_only_shows_title_and_preserves_manual_alias_hint():
    prompt=rarity_prompt('card',{'UR':[row.version for row in prices()]})
    assert prompt.startswith('## card') and prompt.mode=='markdown'
    assert '请选择罕贵度' not in prompt and '回复编号' not in prompt
    assert 'mqqapi://' in prompt
    old=default('price.heading');old['template']='【{name}｜{rarity}】\n'
    assert upgrade_spec('price.heading',old)['template']==default('price.heading')['template']


def test_default_upgrade_adds_new_version_fields_without_revealing_hidden_old_fields():
    old=default('price.heading');old.update(template='## {name}\n\n',visible=[])
    result=upgrade_spec('price.heading',old)
    assert result['visible']==['rarity']
    assert result['template']=='## {name} | {rarity}\n\n'
    custom=default('price.version');custom.update(template='我的版本 {version}',visible=['version'])
    assert upgrade_spec('price.version',custom)==custom


@pytest.mark.parametrize('status',[400,403])
def test_failed_long_quote_retries_whole_content_and_curve_button(status):
    import json
    from unittest.mock import Mock
    from nonebot.adapters.qq.exception import ActionFailed
    from nonebot.drivers import Response
    from hikari_bot.qq_official.prices import PriceQuote
    from hikari_bot.qq_official.price_history import HistoryButtons
    from hikari_bot.qq_official.delivery import context
    async def run():
        pages=format_comparison('card','UR',prices()*20)
        assert len(pages)==1 and len(pages[0])>2800 and pages[0].count('## card | UR')==1
        quote=PriceQuote(pages[0],'markdown','card',prices()*20)
        client=transport()
        client._request.side_effect=[ActionFailed(Response(status,content=json.dumps({'code':304003,'message':'rejected'}))),
                                     {'id':'notice','timestamp':NOW.isoformat()},
                                     {'id':'retried','timestamp':NOW.isoformat()}]
        bot=NamedFileBot(client);event=source('c2c');pager=ReplyPager(now=lambda:NOW)
        keyboard=HistoryButtons(Mock(),None).quote_keyboard(bot,event,quote)
        await pager.send(bot,event,quote,keyboard=keyboard)
        sent=messages(client)
        assert len(sent)==2 and sent[0].json['markdown']['content']==quote
        assert sent[0].json['keyboard']['content']['rows'][0]['buttons'][0]['render_data']['label']=='卡价曲线'
        pending=pager.pending[context(bot,event)]
        assert pending.offset==0 and pending.text==quote and pending.keyboard==keyboard
        retry=source('c2c');retry.id='retry'
        await pager.send(bot,retry)
        assert messages(client)[-1].json['markdown']==sent[0].json['markdown']
        assert messages(client)[-1].json['keyboard']==sent[0].json['keyboard']
        assert not pager.pending
    asyncio.run(run())
