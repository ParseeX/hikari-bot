"""实际 PAC1 原画/异画命名差异：报价和曲线必须指向各自商品。"""
import asyncio
from unittest.mock import AsyncMock, Mock

from hikari_bot.features.card_prices.models import CardVersion
from hikari_bot.features.card_prices.service import ComparisonService
from hikari_bot.features.cardrush.models import PriceRecord
from hikari_bot.features.cardrush.repository import PriceRepository
from hikari_bot.features.cardrush.read_only import ReadOnlyPrices
from hikari_bot.services.price_reports import PriceReports


def test_urara_three_pser_versions_match_distinct_histories_without_writing(tmp_path):
    path = tmp_path/'prices.db'
    repo = PriceRepository(path)
    repo.save_prices([
        PriceRecord(108273, '灰流うらら', 3000, 'プリズマティックシークレット', 'LPST-JP009', None),
        PriceRecord(47814, '灰流うらら(立ち)', 5000, 'プリズマティックシークレット', 'PAC1-JP016', None),
        PriceRecord(47815, '[新]灰流うらら(座り)', 8000, 'プリズマティックシークレット', 'PAC1-JP016', None),
        PriceRecord(47735, '灰流うらら(立ち)', 300, 'シークレット', 'PAC1-JP016', None),
        PriceRecord(99991, '灰流うらら(立ち)', 9999, 'プリズマティックシークレット', 'PAC1-JP016X', None),
        PriceRecord(99992, '別のカード(立ち)', 9999, 'プリズマティックシークレット', 'PAC1-JP016', None),
    ], observed_at='2026-11-18T00:00:00Z')
    before = path.read_bytes()
    versions = [CardVersion(500828, 'LPST-JP009', 'PSER'),
                CardVersion(61639, 'PAC1-JP016', 'PSER'),
                CardVersion(61767, 'PAC1-JP016(异画)', 'PSER')]
    async def run():
        reader = ReadOnlyPrices(path)
        reports = PriceReports(reader, None)
        found = [await reports.exact_version('灰流うらら', v) for v in versions]
        assert [[r.product_id for r in rows] for rows in found] == [[108273], [47814], [47815]]
        rows = await ComparisonService(Mock(prices=AsyncMock(return_value={})), reader).compare('灰流うらら', versions)
        assert [[r.product_id for r in row.cardrush] for row in rows] == [[108273], [47814], [47815]]
        for name, number in [('灰流うらら', 'PAC1-JP016(异画2)'), ('別のカード', 'PAC1-JP016(异画)'),
                             ('灰流うらら', 'PAC1-JP017(异画)'), ('灰流うらら', 'PAC1-JP016(亚版)')]:
            assert await reports.exact_version(name, CardVersion(1, number, 'PSER')) == []
        assert [p.price for p in await reader.get_price_history(found[2][0].product_id)] == [8000]
    asyncio.run(run())
    assert path.read_bytes() == before
