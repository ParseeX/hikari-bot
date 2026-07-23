import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from .errors import CardrushRepositoryError
from .models import PriceChange, PricePoint, PriceRecord, PriceSnapshot


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

    def initialize(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as connection:
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
        except sqlite3.Error as exc:
            raise CardrushRepositoryError(
                f"Cardrush database initialization failed: {exc}"
            ) from exc

    def reset(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as connection:
                connection.execute("DROP TABLE IF EXISTS card_price_history")
            self.initialize()
        except sqlite3.Error as exc:
            raise CardrushRepositoryError(
                f"Cardrush database reset failed: {exc}"
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
            WHERE product_id = ?
            ORDER BY changed_at DESC, id DESC
            LIMIT 1
            """,
            (product_id,),
        )
        row = cursor.fetchone()
        return (int(row[0]), row[1]) if row else None

    def save_prices(self, records: Sequence[PriceRecord]) -> int:
        self.initialize()
        now_str = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        seen_product_ids: set[int] = set()
        count = 0

        try:
            with sqlite3.connect(self.db_path) as connection:
                cursor = connection.cursor()
                for record in records:
                    seen_product_ids.add(record.product_id)
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
                            record.updated_at or now_str,
                        ),
                    )
                    count += 1

                cursor.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            product_id,
                            name,
                            rarity,
                            model_number,
                            price,
                            ROW_NUMBER() OVER (
                                PARTITION BY product_id
                                ORDER BY changed_at DESC, id DESC
                            ) AS rn
                        FROM card_price_history
                    )
                    SELECT product_id, name, rarity, model_number
                    FROM ranked
                    WHERE rn = 1 AND price != 0
                    """
                )
                for product_id, name, rarity, model_number in cursor.fetchall():
                    if product_id in seen_product_ids:
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
                        VALUES (?, ?, ?, ?, 0, ?)
                        """,
                        (
                            product_id,
                            name,
                            rarity,
                            model_number,
                            now_str,
                        ),
                    )
                    count += 1
            return count
        except sqlite3.Error as exc:
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
            with sqlite3.connect(self.db_path) as connection:
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
                        WHERE {where}
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
        except sqlite3.Error as exc:
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
            with sqlite3.connect(self.db_path) as connection:
                rows = connection.execute(
                    """
                    SELECT price, changed_at
                    FROM card_price_history
                    WHERE product_id = ?
                    ORDER BY changed_at ASC, id ASC
                    """,
                    (product_id,),
                ).fetchall()
        except sqlite3.Error as exc:
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
            with sqlite3.connect(self.db_path) as connection:
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
                        WHERE DATE(h1.changed_at) = DATE(?)
                          AND h1.id = (
                              SELECT MAX(id)
                              FROM card_price_history h2
                              WHERE h2.product_id = h1.product_id
                                AND DATE(h2.changed_at) = DATE(?)
                          )
                    ),
                    prev_last AS (
                        SELECT product_id, price AS old_price
                        FROM card_price_history h3
                        WHERE DATE(h3.changed_at) < DATE(?)
                          AND h3.id = (
                              SELECT MAX(id)
                              FROM card_price_history h4
                              WHERE h4.product_id = h3.product_id
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
        except sqlite3.Error as exc:
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
                if abs(price_diff) < min_abs_diff:
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
            with sqlite3.connect(self.db_path) as connection:
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
        except sqlite3.Error as exc:
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
