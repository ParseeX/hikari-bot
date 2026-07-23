import sqlite3

from hikari_bot.features.cardrush.models import PriceRecord
from hikari_bot.features.cardrush.repository import PriceRepository


def record(product_id: int, price: int, updated_at: str) -> PriceRecord:
    return PriceRecord(
        product_id=product_id,
        name=f"card-{product_id}",
        price=price,
        rarity="ウルトラ",
        model_number="TEST-JP001",
        updated_at=updated_at,
    )


def test_initialize_preserves_current_schema(tmp_path):
    db_path = tmp_path / "prices.db"
    repository = PriceRepository(db_path)
    repository.initialize()

    with sqlite3.connect(db_path) as connection:
        schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name IN ("
            "'card_price_history', "
            "'idx_card_price_history_product_time', "
            "'idx_card_price_history_changed_at'"
            ") ORDER BY type, name"
        ).fetchall()

    assert len(schema) == 3
    assert "product_id INTEGER NOT NULL" in schema[-1][2]
    assert "changed_at TEXT NOT NULL" in schema[-1][2]


def test_save_records_only_on_change_and_marks_missing_as_zero(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    first = [
        record(1, 1000, "2026-07-22T00:00:00.000Z"),
        record(2, 2000, "2026-07-22T00:00:00.000Z"),
    ]
    assert repository.save_prices(first) == 2
    assert repository.save_prices(first) == 0

    second = [record(1, 1500, "2026-07-23T00:00:00.000Z")]
    assert repository.save_prices(second) == 2

    assert [point.price for point in repository.get_history(1)] == [1000, 1500]
    assert repository.get_history(2)[-1].price == 0


def test_search_and_daily_changes_return_models(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.save_prices(
        [record(1, 1000, "2026-07-22T00:00:00.000Z")]
    )
    repository.save_prices(
        [record(1, 1500, "2026-07-23T00:00:00.000Z")]
    )

    result = repository.search_latest("card-1")
    changes = repository.get_daily_changes("2026-07-23")

    assert result[0].price == 1500
    assert changes[0].old_price == 1000
    assert changes[0].new_price == 1500
    assert changes[0].price_diff == 500
