import pytest

from hikari_bot.persistence.database import StateDatabase


def test_connect_creates_parent_and_configures_sqlite(tmp_path):
    database = StateDatabase(tmp_path / "nested" / "hikari.db")

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_transaction_rolls_back_on_error(tmp_path):
    database = StateDatabase(tmp_path / "hikari.db")

    with database.connect() as connection:
        connection.execute("CREATE TABLE values_table (value TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES ('kept-out')")
            raise RuntimeError("测试回滚")

    with database.connect() as connection:
        assert connection.execute("SELECT value FROM values_table").fetchall() == []
