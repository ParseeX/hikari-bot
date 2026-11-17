"""真实官方入口：报价按钮精确到版本，曲线选择分页，Markdown 图文同发。"""
import subprocess
import sys

from test_catalog_service import catalog


def test_official_price_to_exact_curve_and_manual_curve_choices(catalog):
    script = r'''
import asyncio,sys
from pathlib import Path
from decimal import Decimal
from unittest.mock import AsyncMock,Mock
sys.path.insert(0,'tests')
import nonebot
nonebot.logger.remove();nonebot.init(driver='~aiohttp',_env_file=[],log_level='ERROR')
from hikari_bot.qq_official import plugin
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.prices import PriceQueries
from hikari_bot.qq_official.media import NamedFileBot
from hikari_bot.services.price_reports import PriceReports
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.features.card_prices.models import CardVersion,JhsPrice
from hikari_bot.features.card_prices.service import Comparison
from hikari_bot.features.cardrush.models import PriceSnapshot,PricePoint
from hikari_bot.features.cardrush.reporting import chart
from test_qq_alias_choices import source,click,messages
from test_qq_art_markdown import gallery_transport,picture
chart.draw_price_chart=Mock(return_value=picture(1))
controllers=[];factory=plugin.CallbackController
def controller(*a,**kw):
 value=factory(*a,**kw);controllers.append(value);return value
plugin.CallbackController=controller
catalog=CardCatalog(Path(sys.argv[1]));name=catalog.search('1234')['jp_name']
config=PilotConfig('synthetic-app','fake',users=frozenset({'only-user','other'}),accept_joined_groups=True,catalog_path=catalog.path)
def button_list(payload):return [b for row in payload['keyboard']['content']['rows'] for b in row['buttons']]
async def run():
 for kind in ['c2c','group','group-no-at']:
  for total in [1,2]:
   versions=[CardVersion(i+1,f'BOX-JP00{i+1}','UR',name) for i in range(total)]
   products=[PriceSnapshot(i+10,name,'ウルトラ',v.number,800,'2026-11-18') for i,v in enumerate(versions)]
   distractors=[PriceSnapshot(900,'wrong card','ウルトラ','BOX-JP001',999,'2026-11-18'),
                PriceSnapshot(901,name,'シークレット','BOX-JP001',999,'2026-11-18'),
                PriceSnapshot(902,name,'ウルトラ','BOX-JP001X',999,'2026-11-18')]
   reader=Mock(search_prices=AsyncMock(return_value=distractors+products),get_price_history=AsyncMock(return_value=[PricePoint(500,'2026-11-17'),PricePoint(800,'2026-11-18')]))
   backend=PriceQueries(None,None,None)
   backend.comparison=Mock(versions=AsyncMock(return_value=versions),compare=AsyncMock(return_value=[Comparison(v,JhsPrice(v.id,Decimal('3'),None,None),()) for v in versions]))
   handler=plugin.register(config,queries=Queries(catalog,price_reports=PriceReports(reader,catalog,selection_limit=100)),price_backend=backend).handlers[0].call
   ctl=controllers[-1];client=gallery_transport();client.put_interaction=AsyncMock();seq=0
   async def press(button, actor='only-user'):
    nonlocal seq
    seq+=1
    updates={'user_openid':actor} if kind=='c2c' else {'group_member_openid':actor}
    event=click(button,kind,id=f'click-{seq}',**updates);event.data.resolved.message_id='reply'
    await ctl.handle(NamedFileBot(client),event)
    await asyncio.gather(*ctl.tasks)
    return messages(client)[-1].json
   incoming=source(kind);incoming.content='卡价 1234'
   await handler(client,incoming)
   quote=await press(button_list(messages(client)[-1].json)[0])
   assert '罕贵度：' not in quote['markdown']['content']
   assert f'## {name} · UR' in quote['markdown']['content'] and '### BOX-JP001' in quote['markdown']['content']
   curve_button=button_list(quote)[0]
   assert curve_button['render_data']['label']=='卡价曲线'
   actor='other' if kind!='c2c' else 'only-user'
   result=await press(curve_button,actor)
   if total>1:
    assert [b['render_data']['label'] for b in button_list(result)]==['UR · BOX-JP001','UR · BOX-JP002']
    result=await press(button_list(result)[1],actor)
   reader.get_price_history.assert_awaited_once_with(products[total-1].product_id)
   content=result['markdown']['content']
   assert f'## {name} · UR' in content and f'### BOX-JP00{total}' in content
   assert 'Cardrush 买取价历史' in content and '（日元）' not in content
   assert content.count('qqbot-media://')==1 and content.index('最近买取价')<content.index('qqbot-media://')
   assert '添加别名' not in content and result['msg_type']==2
   # 无同版本历史不能偷换到同盒、同名或其他罕贵。
   reader.search_prices.return_value=distractors
   incoming=source(kind);incoming.id='new-quote';incoming.content='卡价 1234'
   await handler(client,incoming)
   quote=await press(button_list(messages(client)[-1].json)[0])
   result=await press(button_list(quote)[0])
   if total>1:result=await press(button_list(result)[0])
   assert result['content']=='暂无该版本的 Cardrush 买取价历史记录。'
   assert reader.get_price_history.await_count==1
   # 手动曲线查询九个版本，最后一项仍能从下一页到达。
   all_products=[PriceSnapshot(i+30,name,'ウルトラ',f'MANY-JP{i:03}',800,'2026-11-18') for i in range(9)]
   reader.search_prices.return_value=all_products
   incoming=source(kind);incoming.id='manual-curve';incoming.content='卡价曲线 1234'
   await handler(client,incoming)
   menu=messages(client)[-1].json
   assert '回复编号' not in menu['markdown']['content']
   assert len(button_list(menu))==9
   menu=await press(button_list(menu)[-1])
   assert button_list(menu)[0]['render_data']['label']=='UR · MANY-JP008'
   await press(button_list(menu)[0])
   assert reader.get_price_history.await_args.args==(38,)
   incoming=source(kind);incoming.id='manual-again';incoming.content='卡价曲线 1234'
   await handler(client,incoming)
   incoming=source(kind);incoming.id='number-selection';incoming.content='2'
   await handler(client,incoming)
   assert reader.get_price_history.await_args.args==(31,)
asyncio.run(run())
'''
    result=subprocess.run([sys.executable,'-c',script,str(catalog.path)],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr[-5000:]+result.stdout[-3000:]


def test_exact_history_mapping_is_read_only_and_rejects_other_versions(tmp_path):
    import asyncio
    import sqlite3
    from hikari_bot.features.card_prices.models import CardVersion
    from hikari_bot.features.cardrush.read_only import ReadOnlyPrices
    from hikari_bot.services.price_reports import PriceReports
    path=tmp_path/'prices.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE card_price_history(id INTEGER PRIMARY KEY,product_id INTEGER,name TEXT,rarity TEXT,model_number TEXT,price INTEGER,changed_at TEXT)')
        db.executemany('INSERT INTO card_price_history VALUES(?,?,?,?,?,?,?)',[
            (1,10,'card','ウルトラ','TEST-JP001',100,'2026-11-16'),
            (2,10,'card','ウルトラ','TEST-JP001',200,'2026-11-18'),
            (3,11,'card','シークレット','TEST-JP001',300,'2026-11-18'),
            (4,12,'different card','ウルトラ','TEST-JP001',400,'2026-11-18'),
            (5,13,'card','ウルトラ','TEST-JP001X',500,'2026-11-18')])
    before=path.read_bytes()
    reports=PriceReports(ReadOnlyPrices(path),None)
    found=asyncio.run(reports.exact_version('card',CardVersion(1,'TEST-JP001','UR')))
    assert [r.product_id for r in found]==[10]
    assert asyncio.run(reports.exact_version('card',CardVersion(2,'TEST-JP002','UR')))==[]
    assert path.read_bytes()==before


def test_unknown_expired_or_foreign_curve_target_never_queries_history():
    import asyncio
    from unittest.mock import Mock,AsyncMock
    from types import SimpleNamespace
    from hikari_bot.qq_official.price_history import HistoryButtons
    from hikari_bot.qq_official.events import request_from_event
    from test_qq_alias_choices import source
    from hikari_bot.features.card_prices.models import CardVersion
    async def run():
        reports=Mock(exact_version=AsyncMock(),sessions={});clock=[0]
        nav=HistoryButtons(reports,Mock(),clock=lambda:clock[0])
        bot=SimpleNamespace(self_id='synthetic-app',interaction=object())
        incoming=source('group');req=request_from_event(bot,incoming)
        command=nav.bind(bot,incoming,'versions',(CardVersion(1,'TEST-JP001','UR'),),'card')
        _,token=command.split()
        for scenario in ['unknown','foreign','expired','typed']:
            event=source('group');value=token
            if scenario=='unknown':value='x'*24
            if scenario=='foreign':event.group_openid='other-group'
            if scenario=='expired':clock[0]=601
            if scenario=='typed':clock[0]=0;bot.interaction=None
            assert await nav.handle(bot,event,req,'卡价曲线图',value)
        reports.exact_version.assert_not_awaited()
    asyncio.run(run())
