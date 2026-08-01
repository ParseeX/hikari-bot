import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from hikari_bot.persistence.database import PersistenceError, configure_sqlite_connection

from .errors import CardrushRepositoryError
from .models import (
    DatabaseRepairResult,
    PriceChange,
    PricePoint,
    PriceRecord,
    PriceSnapshot,
)


def _build_series_where(
    series_keywords: Iterable[str] | None,
) -> tuple[str, list[str]]:
    if not series_keywords:
        return "", []
    keywords = [value.strip() for value in series_keywords if value.strip()]
    if not keywords:
        return "", []
    clauses = ["model_number LIKE ?" for _ in keywords]
    return (
        " AND (" + " OR ".join(clauses) + ")",
        [f"%{value}%" for value in keywords],
    )


class PriceRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        """创建使用统一 SQLite 并发策略的 Cardrush 专用连接。"""
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        configure_sqlite_connection(connection)
        return connection

    def initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS card_price_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        rarity TEXT,
                        model_number TEXT,
                        price INTEGER NOT NULL,
                        changed_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_card_price_history_product_time
                    ON card_price_history(product_id, changed_at, id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS
                    idx_card_price_history_changed_at
                    ON card_price_history(changed_at)
                    """
                )
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush database initialization failed: {exc}"
            ) from exc

    def reset(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("DROP TABLE IF EXISTS card_price_history")
            self.initialize()
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush database reset failed: {exc}"
            ) from exc

    def repair_legacy_history(self) -> DatabaseRepairResult:
        """备份并清理旧版本写入的 0 价格和连续重复价格点。"""
        self.initialize()
        database_path = Path(self.db_path)
        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S%f"
        )
        backup_path = database_path.with_name(
            f"{database_path.stem}.pre-repair-{timestamp}"
            f"{database_path.suffix}"
        )

        try:
            source = self._connect()
            backup = sqlite3.connect(str(backup_path), timeout=5.0)
            try:
                source.backup(backup)
            finally:
                backup.close()
                source.close()

            with self._connect() as connection:
                zero_cursor = connection.execute(
                    "DELETE FROM card_price_history WHERE price <= 0"
                )
                removed_zero_rows = zero_cursor.rowcount

                duplicate_ids = [
                    row[0]
                    for row in connection.execute(
                        """
                        WITH valid AS (
                            SELECT
                                id,
                                price,
                                LAG(price) OVER (
                                    PARTITION BY product_id
                                    ORDER BY id
                                ) AS previous_price
                            FROM card_price_history
                            WHERE price > 0
                        )
                        SELECT id
                        FROM valid
                        WHERE price = previous_price
                        """
                    ).fetchall()
                ]
                for row_id in duplicate_ids:
                    connection.execute(
                        "DELETE FROM card_price_history WHERE id = ?",
                        (row_id,),
                    )

            return DatabaseRepairResult(
                backup_path=str(backup_path),
                removed_zero_rows=max(removed_zero_rows, 0),
                removed_duplicate_rows=len(duplicate_ids),
            )
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush database repair failed: {exc}"
            ) from exc

    @staticmethod
    def _get_latest_price(
        cursor: sqlite3.Cursor,
        product_id: int,
    ) -> tuple[int, str] | None:
        cursor.execute(
            """
            SELECT price, changed_at
            FROM card_price_history
            WHERE product_id = ? AND price > 0
            ORDER BY changed_at DESC, id DESC
            LIMIT 1
            """,
            (product_id,),
        )
        row = cursor.fetchone()
        return (int(row[0]), row[1]) if row else None

    def save_prices(
        self,
        records: Sequence[PriceRecord],
        *,
        observed_at: str | None = None,
    ) -> int:
        self.initialize()
        # Cardrush 的 updated_at 是页面更新时间，不能作为单卡历史时间。
        observation_time = observed_at or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        count = 0

        try:
            with self._connect() as connection:
                cursor = connection.cursor()
                for record in records:
                    if record.price <= 0:
                        continue
                    latest = self._get_latest_price(
                        cursor,
                        record.product_id,
                    )
                    if latest and latest[0] == record.price:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO card_price_history(
                            product_id,
                            name,
                            rarity,
                            model_number,
                            price,
                            changed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.product_id,
                            record.name,
                            record.rarity,
                            record.model_number,
                            record.price,
                            observation_time,
                        ),
                    )
                    count += 1

            return count
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush price save failed: {exc}"
            ) from exc

    def search_latest(
        self,
        name: str,
        rarity: str | list[str] | None = None,
        model_number: str | None = None,
        limit: int = 10,
    ) -> list[PriceSnapshot]:
        self.initialize()
        conditions = ["name LIKE ?"]
        params: list[object] = [f"%{name}%"]

        if isinstance(rarity, list):
            if rarity:
                placeholders = ",".join("?" for _ in rarity)
                conditions.append(f"rarity IN ({placeholders})")
                params.extend(rarity)
            else:
                conditions.append("0")
        elif rarity is not None:
            conditions.append("IFNULL(rarity, '') = IFNULL(?, '')")
            params.append(rarity)
        if model_number is not None:
            conditions.append("model_number LIKE ?")
            params.append(f"%{model_number}%")

        where = " AND ".join(conditions)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    WITH ranked AS (
                        SELECT
                            product_id,
                            name,
                            rarity,
                            model_number,
                            price,
                            changed_at,
                            ROW_NUMBER() OVER (
                                PARTITION BY product_id
                                ORDER BY changed_at DESC, id DESC
                            ) AS rn
                        FROM card_price_history
                        WHERE {where} AND price > 0
                    )
                    SELECT
                        product_id,
                        name,
                        rarity,
                        model_number,
                        price,
                        changed_at
                    FROM ranked
                    WHERE rn = 1
                    ORDER BY price DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush price search failed: {exc}"
            ) from exc

        return [
            PriceSnapshot(
                product_id=product_id,
                name=name_value,
                rarity=rarity_value,
                model_number=model_value,
                price=int(price),
                changed_at=changed_at,
            )
            for (
                product_id,
                name_value,
                rarity_value,
                model_value,
                price,
                changed_at,
            ) in rows
        ]

    def get_history(self, product_id: int) -> list[PricePoint]:
        self.initialize()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT price, changed_at
                    FROM card_price_history
                    WHERE product_id = ? AND price > 0
                    ORDER BY changed_at ASC, id ASC
                    """,
                    (product_id,),
                ).fetchall()
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush price history query failed: {exc}"
            ) from exc
        return [
            PricePoint(price=int(price), changed_at=changed_at)
            for price, changed_at in rows
        ]

    def get_daily_changes(
        self,
        date_str: str | None = None,
        series_keywords: Iterable[str] | None = None,
        min_abs_diff: int = 0,
        include_new: bool = True,
        exclude_prefixes: Iterable[str] | None = None,
    ) -> list[PriceChange]:
        self.initialize()
        if date_str is None:
            date_str = date.today().isoformat()
        series_where, series_params = _build_series_where(series_keywords)

        exclude_list = [value for value in (exclude_prefixes or []) if value]
        if exclude_list:
            exclude_clauses = " AND ".join(
                "(model_number IS NULL OR model_number NOT LIKE ?)"
                for _ in exclude_list
            )
            exclude_where = f" AND ({exclude_clauses})"
            exclude_params = [f"{value}%" for value in exclude_list]
        else:
            exclude_where = ""
            exclude_params = []

        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    WITH
                    day_last AS (
                        SELECT
                            product_id,
                            name,
                            rarity,
                            model_number,
                            price AS new_price,
                            changed_at
                        FROM card_price_history h1
                        WHERE h1.price > 0
                          AND DATE(h1.changed_at) = DATE(?)
                          AND h1.id = (
                              SELECT MAX(id)
                              FROM card_price_history h2
                              WHERE h2.product_id = h1.product_id
                                AND h2.price > 0
                                AND DATE(h2.changed_at) = DATE(?)
                          )
                    ),
                    prev_last AS (
                        SELECT product_id, price AS old_price
                        FROM card_price_history h3
                        WHERE h3.price > 0
                          AND DATE(h3.changed_at) < DATE(?)
                          AND h3.id = (
                              SELECT MAX(id)
                              FROM card_price_history h4
                              WHERE h4.product_id = h3.product_id
                                AND h4.price > 0
                                AND DATE(h4.changed_at) < DATE(?)
                          )
                    )
                    SELECT
                        d.product_id,
                        d.name,
                        d.rarity,
                        d.model_number,
                        p.old_price,
                        d.new_price,
                        d.changed_at
                    FROM day_last d
                    LEFT JOIN prev_last p ON p.product_id = d.product_id
                    WHERE 1 = 1
                    {series_where}
                    {exclude_where}
                      AND d.new_price BETWEEN 500 AND 100000
                    ORDER BY d.product_id DESC, d.name
                    """,
                    [
                        date_str,
                        date_str,
                        date_str,
                        date_str,
                        *series_params,
                        *exclude_params,
                    ],
                ).fetchall()
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush daily changes query failed: {exc}"
            ) from exc

        changes: list[PriceChange] = []
        for (
            product_id,
            name,
            rarity,
            model_number,
            old_price,
            new_price,
            changed_at,
        ) in rows:
            old_value = int(old_price) if old_price is not None else None
            new_value = int(new_price)
            if old_value is None:
                if not include_new:
                    continue
                price_diff = None
                percent_diff = None
                change_type = "new"
            else:
                price_diff = new_value - old_value
                if price_diff == 0 or abs(price_diff) < min_abs_diff:
                    continue
                percent_diff = (
                    price_diff / old_value * 100 if old_value else None
                )
                change_type = "changed"
            changes.append(
                PriceChange(
                    product_id=product_id,
                    name=name,
                    rarity=rarity,
                    model_number=model_number,
                    old_price=old_value,
                    new_price=new_value,
                    change_type=change_type,
                    price_diff=price_diff,
                    percent_diff=percent_diff,
                    changed_at=changed_at,
                )
            )
        return changes

    def get_series_latest(
        self,
        series_keywords: Iterable[str],
        limit: int = 100,
    ) -> list[PriceSnapshot]:
        self.initialize()
        series_where, params = _build_series_where(series_keywords)
        if not series_where:
            raise ValueError("series_keywords 不能为空")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    WITH ranked AS (
                        SELECT
                            product_id,
                            name,
                            rarity,
                            model_number,
                            price,
                            changed_at,
                            ROW_NUMBER() OVER (
                                PARTITION BY product_id
                                ORDER BY changed_at DESC, id DESC
                            ) AS rn
                        FROM card_price_history
                        WHERE price > 0
                    )
                    SELECT
                        product_id,
                        name,
                        rarity,
                        model_number,
                        price,
                        changed_at
                    FROM ranked
                    WHERE rn = 1
                    {series_where}
                    ORDER BY price DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush series price query failed: {exc}"
            ) from exc
        return [
            PriceSnapshot(
                product_id=product_id,
                name=name,
                rarity=rarity,
                model_number=model_number,
                price=int(price),
                changed_at=changed_at,
            )
            for (
                product_id,
                name,
                rarity,
                model_number,
                price,
                changed_at,
            ) in rows
        ]
