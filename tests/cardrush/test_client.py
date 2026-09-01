from pathlib import Path

import pytest

from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.errors import CardrushClientError
from hikari_bot.features.cardrush.models import PriceRecord


def test_extract_data_returns_typed_price_records():
    html = Path("tests/cardrush/fixtures/cardrush_page.html").read_text(
        encoding="utf-8"
    )
    records = CardrushClient.extract_records(html)

    assert [record.product_id for record in records] == [101, 102]
    assert records[0].name == "青眼の白龍"
    assert records[0].price == 3200


def test_extract_data_wraps_missing_next_data():
    with pytest.raises(CardrushClientError, match="__NEXT_DATA__"):
        CardrushClient.extract_records("<html></html>")


def test_query_all_rejects_empty_first_page():
    client = CardrushClient("https://example.test", {}, None)
    client.query = lambda **kwargs: []

    with pytest.raises(CardrushClientError, match="no price records"):
        client.query_all()


def test_query_all_fetches_pages_of_100_records():
    client = CardrushClient("https://example.test", {}, None)
    calls = []
    record = PriceRecord(1, "card", 1000, None, None, None)

    def fake_query(**kwargs):
        calls.append(kwargs)
        return [record] * (100 if kwargs["page"] == 1 else 2)

    client.query = fake_query

    records = client.query_all()

    assert len(records) == 102
    assert calls == [
        {"limit": 100, "page": 1},
        {"limit": 100, "page": 2},
    ]
