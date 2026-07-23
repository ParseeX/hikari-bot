from dataclasses import FrozenInstanceError

import pytest

from hikari_bot.features.cardrush.errors import (
    CardrushClientError,
    CardrushError,
    CardrushRenderError,
    CardrushRepositoryError,
)
from hikari_bot.features.cardrush.models import PriceRecord


def test_price_record_round_trips_mapping():
    raw = {
        "product_id": "42",
        "name": "青眼の白龍",
        "price": "3200",
        "rarity": "ウルトラ",
        "model_number": "QCAC-JP001",
        "updated_at": "2026-07-23T00:00:00.000Z",
    }

    record = PriceRecord.from_mapping(raw)

    assert record.product_id == 42
    assert record.price == 3200
    assert record.to_mapping() == {
        "product_id": 42,
        "name": "青眼の白龍",
        "price": 3200,
        "rarity": "ウルトラ",
        "model_number": "QCAC-JP001",
        "updated_at": "2026-07-23T00:00:00.000Z",
    }


def test_price_record_is_immutable():
    record = PriceRecord(1, "card", 100, None, None, None)
    with pytest.raises(FrozenInstanceError):
        record.price = 200


def test_specific_errors_share_cardrush_base():
    assert issubclass(CardrushClientError, CardrushError)
    assert issubclass(CardrushRepositoryError, CardrushError)
    assert issubclass(CardrushRenderError, CardrushError)
