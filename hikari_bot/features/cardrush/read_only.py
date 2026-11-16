"""Shared latest-price query plus a read-only srbs repository."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from .models import PriceSnapshot
from .errors import CardrushRepositoryError

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

class ReadOnlyPrices:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        pass

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            connection.execute('PRAGMA query_only=ON')
            yield connection
        finally:
            connection.close()

    async def search_prices(self, name, rarity=None, model_number=None, limit=10):
        return await asyncio.to_thread(search_latest, self, name, rarity, model_number, limit)

    async def get_price_history(self, product_id):
        from .repository import PriceRepository
        return await asyncio.to_thread(PriceRepository.get_history, self, product_id)

    async def get_daily_changes(self, date, *, exclude_prefixes=None):
        from .repository import PriceRepository
        return await asyncio.to_thread(PriceRepository.get_daily_changes, self, date,
                                      None, 0, True, exclude_prefixes)
