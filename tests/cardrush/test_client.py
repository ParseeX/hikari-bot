from pathlib import Path

import pytest

from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.errors import CardrushClientError


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
