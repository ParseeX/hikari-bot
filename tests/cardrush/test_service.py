import asyncio

from hikari_bot.features.cardrush.models import (
    PricePoint,
    PriceRecord,
    PriceSnapshot,
)
from hikari_bot.features.cardrush.service import CardrushService


class FakeClient:
    def query_all(self):
        return [
            PriceRecord(
                1,
                "card",
                1000,
                None,
                None,
                "2026-07-23T00:00:00.000Z",
            )
        ]


class FakeRepository:
    def __init__(self):
        self.saved = []

    def search_latest(
        self,
        name,
        rarity=None,
        model_number=None,
        limit=10,
    ):
        return [
            PriceSnapshot(
                1,
                name,
                rarity,
                model_number,
                1000,
                "2026-07-23",
            )
        ]

    def get_history(self, product_id):
        return [
            PricePoint(900, "2026-07-22"),
            PricePoint(1000, "2026-07-23"),
        ]

    def get_daily_changes(
        self,
        date,
        series_keywords=None,
        min_abs_diff=0,
        include_new=True,
        exclude_prefixes=None,
    ):
        return []

    def save_prices(self, records):
        self.saved = list(records)
        return len(self.saved)

    def reset(self):
        return None


def test_service_exposes_async_search_and_history():
    service = CardrushService(FakeRepository(), FakeClient())
    result = asyncio.run(
        service.search_prices("青眼の白龍", rarity="ウルトラ")
    )
    history = asyncio.run(service.get_price_history(1))

    assert result[0].name == "青眼の白龍"
    assert result[0].rarity == "ウルトラ"
    assert [point.price for point in history] == [900, 1000]


def test_refresh_fetches_and_saves_records():
    repository = FakeRepository()
    service = CardrushService(repository, FakeClient())

    assert asyncio.run(service.refresh_prices()) == 1
    assert repository.saved[0].product_id == 1
