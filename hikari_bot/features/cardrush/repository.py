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


def _history_sort_key(changed_at: str, row_id: int) -> tuple[int, float, int]:
    """按真实 UTC 时间排序，兼容旧数据中的 Z 和带时区偏移格式。"""
    try:
        value = datetime.fromisoformat(changed_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (0, value.astimezone(timezone.utc).timestamp(), row_id)
    except (TypeError, ValueError):
        return (1, 0.0, row_id)


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
        restored_from: str | None = None
        current_count = self._history_row_count(database_path)
        source_backup = self._find_repair_source(
            database_path,
            current_count,
        )
        timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d%H%M%S%f"
        )
        backup_path = database_path.with_name(
            f"{database_path.stem}.pre-repair-{timestamp}"
            f"{database_path.suffix}"
        )

        try:
            self._backup_database(backup_path)
            if source_backup is not None:
                self._restore_database(source_backup)
                restored_from = str(source_backup)

            with self._connect() as connection:
                zero_cursor = connection.execute(
                    "DELETE FROM card_price_history WHERE price <= 0"
                )
                removed_zero_rows = zero_cursor.rowcount

                rows = connection.execute(
                    """
                    SELECT id, product_id, price, changed_at
                    FROM card_price_history
                    WHERE price > 0
                    """
                ).fetchall()
                product_rows: dict[int, list[tuple[int, int, str]]] = {}
                for row_id, product_id, price, changed_at in rows:
                    product_rows.setdefault(product_id, []).append(
                        (row_id, price, changed_at)
                    )

                duplicate_ids: list[int] = []
                for product_history in product_rows.values():
                    previous_price: int | None = None
                    for row_id, price, changed_at in sorted(
                        product_history,
                        key=lambda row: _history_sort_key(row[2], row[0]),
                    ):
                        if price == previous_price:
                            duplicate_ids.append(row_id)
                        else:
                            previous_price = price
                for row_id in duplicate_ids:
                    connection.execute(
                        "DELETE FROM card_price_history WHERE id = ?",
                        (row_id,),
                    )

            return DatabaseRepairResult(
                backup_path=str(backup_path),
                removed_zero_rows=max(removed_zero_rows, 0),
                removed_duplicate_rows=len(duplicate_ids),
                restored_from=restored_from,
            )
        except (sqlite3.Error, PersistenceError) as exc:
            raise CardrushRepositoryError(
                f"Cardrush database repair failed: {exc}"
            ) from exc

    def _backup_database(self, backup_path: Path) -> None:
        source = self._connect()
        backup = sqlite3.connect(str(backup_path), timeout=5.0)
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()

    def _restore_database(self, source_path: Path) -> None:
        source = sqlite3.connect(str(source_path), timeout=5.0)
        target = self._connect()
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    @staticmethod
    def _history_row_count(database_path: Path) -> int:
        try:
            with sqlite3.connect(str(database_path), timeout=5.0) as connection:
                return int(
                    connection.execute(
                        "SELECT COUNT(*) FROM card_price_history"
                    ).fetchone()[0]
                )
        except sqlite3.Error:
            return 0

    def _find_repair_source(
        self,
        database_path: Path,
        current_count: int,
    ) -> Path | None:
        candidates: list[tuple[int, float, Path]] = []
        pattern = (
            f"{database_path.stem}.pre-repair-*{database_path.suffix}"
        )
        for candidate in database_path.parent.glob(pattern):
            row_count = self._history_row_count(candidate)
            if row_count <= current_count:
                continue
            try:
                modified_at = candidate.stat().st_mtime
            except OSError:
                modified_at = 0.0
            candidates.append((row_count, modified_at, candidate))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

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
            ORDER BY datetime(changed_at) DESC, id DESC
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
                                ORDER BY datetime(changed_at) DESC, id DESC
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
                    ORDER BY datetime(changed_at) ASC, id ASC
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
                              SELECT id
                              FROM card_price_history h2
                              WHERE h2.product_id = h1.product_id
                                AND h2.price > 0
                                AND DATE(h2.changed_at) = DATE(?)
                              ORDER BY datetime(h2.changed_at) DESC, h2.id DESC
                              LIMIT 1
                          )
                    ),
                    prev_last AS (
                        SELECT product_id, price AS old_price
                        FROM card_price_history h3
                        WHERE h3.price > 0
                          AND DATE(h3.changed_at) < DATE(?)
                          AND h3.id = (
                              SELECT id
                              FROM card_price_history h4
                              WHERE h4.product_id = h3.product_id
                                AND h4.price > 0
                                AND DATE(h4.changed_at) < DATE(?)
                              ORDER BY datetime(h4.changed_at) DESC, h4.id DESC
                              LIMIT 1
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
                                ORDER BY datetime(changed_at) DESC, id DESC
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
