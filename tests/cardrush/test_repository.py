import sqlite3
from pathlib import Path

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


def test_initialize_enables_shared_sqlite_wal_policy(tmp_path):
    db_path = tmp_path / "prices.db"
    PriceRepository(db_path).initialize()

    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert journal_mode == "wal"


def test_save_records_only_on_change_and_keeps_missing_cards_unchanged(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    first = [
        record(1, 1000, "source-page-time"),
        record(2, 2000, "source-page-time"),
    ]
    assert repository.save_prices(
        first,
        observed_at="2026-07-22T00:00:00.000Z",
    ) == 2
    assert repository.save_prices(
        first,
        observed_at="2026-07-22T00:15:00.000Z",
    ) == 0

    second = [record(1, 1500, "another-source-page-time")]
    assert repository.save_prices(
        second,
        observed_at="2026-07-23T00:00:00.000Z",
    ) == 1

    assert [point.price for point in repository.get_history(1)] == [1000, 1500]
    assert [point.changed_at for point in repository.get_history(1)] == [
        "2026-07-22T00:00:00.000Z",
        "2026-07-23T00:00:00.000Z",
    ]
    assert [point.price for point in repository.get_history(2)] == [2000]


def test_missing_then_reappearing_at_same_price_does_not_create_fake_change(
    tmp_path,
):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.save_prices(
        [record(1, 1000, "source-time")],
        observed_at="2026-07-31T00:00:00.000Z",
    )
    repository.save_prices([], observed_at="2026-08-01T00:00:00.000Z")
    repository.save_prices(
        [record(1, 1000, "source-time")],
        observed_at="2026-08-01T00:15:00.000Z",
    )

    assert [point.price for point in repository.get_history(1)] == [1000]
    assert repository.get_daily_changes("2026-08-01") == []


def test_zero_price_difference_is_not_reported(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.initialize()
    with sqlite3.connect(tmp_path / "prices.db") as connection:
        connection.executemany(
            """
            INSERT INTO card_price_history(
                product_id, name, rarity, model_number, price, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-07-31T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-08-01T00:00:00.000Z"),
            ],
        )

    assert repository.get_daily_changes("2026-08-01") == []


def test_legacy_zero_points_are_ignored_by_history_queries(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.initialize()
    with sqlite3.connect(tmp_path / "prices.db") as connection:
        connection.executemany(
            """
            INSERT INTO card_price_history(
                product_id, name, rarity, model_number, price, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-07-31T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 0, "2026-08-01T00:00:00.000Z"),
            ],
        )

    assert [point.price for point in repository.get_history(1)] == [1000]
    assert repository.search_latest("card-1")[0].price == 1000


def test_repair_legacy_history_backups_and_removes_invalid_points(tmp_path):
    db_path = tmp_path / "prices.db"
    repository = PriceRepository(db_path)
    repository.initialize()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO card_price_history(
                product_id, name, rarity, model_number, price, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-07-31T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 0, "2026-08-01T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-08-01T00:15:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1300, "2026-08-01T00:30:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1300, "2026-08-01T00:45:00.000Z"),
            ],
        )

    result = repository.repair_legacy_history()

    assert result.removed_zero_rows == 1
    assert result.removed_duplicate_rows == 2
    assert Path(result.backup_path).exists()
    assert [point.price for point in repository.get_history(1)] == [1000, 1300]
    with sqlite3.connect(result.backup_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM card_price_history"
        ).fetchone()[0] == 5

    second_result = repository.repair_legacy_history()

    assert second_result.restored_from == result.backup_path
    assert [point.price for point in repository.get_history(1)] == [1000, 1300]


def test_repair_keeps_earliest_real_timestamp_not_earliest_id(tmp_path):
    db_path = tmp_path / "prices.db"
    repository = PriceRepository(db_path)
    repository.initialize()
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO card_price_history(
                product_id, name, rarity, model_number, price, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "card-1", "SER", "TEST-JP001", 1000, "2026-06-01T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1300, "2026-08-01T00:00:00.000Z"),
                (1, "card-1", "SER", "TEST-JP001", 1300, "2026-07-01T00:00:00.000Z"),
            ],
        )

    repository.repair_legacy_history()

    history = repository.get_history(1)
    assert [(point.price, point.changed_at) for point in history] == [
        (1000, "2026-06-01T00:00:00.000Z"),
        (1300, "2026-07-01T00:00:00.000Z"),
    ]


def test_search_and_daily_changes_return_models(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.save_prices(
        [record(1, 1000, "source-time")],
        observed_at="2026-07-22T00:00:00.000Z",
    )
    repository.save_prices(
        [record(1, 1500, "source-time")],
        observed_at="2026-07-23T00:00:00.000Z",
    )

    result = repository.search_latest("card-1")
    changes = repository.get_daily_changes("2026-07-23")

    assert result[0].price == 1500
    assert changes[0].old_price == 1000
    assert changes[0].new_price == 1500
    assert changes[0].price_diff == 500
