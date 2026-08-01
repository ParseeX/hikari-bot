import asyncio
from collections.abc import Sequence

from .client import CardrushClient
from .models import PriceChange, PricePoint, PriceRecord, PriceSnapshot
from .repository import PriceRepository


class CardrushService:
    def __init__(
        self,
        repository: PriceRepository,
        client: CardrushClient | None = None,
    ) -> None:
        self.repository = repository
        self.client = client

    async def search_prices(
        self,
        name: str,
        rarity: str | list[str] | None = None,
        model_number: str | None = None,
        limit: int = 10,
    ) -> list[PriceSnapshot]:
        return await asyncio.to_thread(
            self.repository.search_latest,
            name,
            rarity,
            model_number,
            limit,
        )

    async def get_price_history(
        self,
        product_id: int,
    ) -> list[PricePoint]:
        return await asyncio.to_thread(
            self.repository.get_history,
            product_id,
        )

    async def get_daily_changes(
        self,
        date: str,
        *,
        series_keywords: list[str] | None = None,
        min_abs_diff: int = 0,
        include_new: bool = True,
        exclude_prefixes: list[str] | None = None,
    ) -> list[PriceChange]:
        return await asyncio.to_thread(
            self.repository.get_daily_changes,
            date,
            series_keywords,
            min_abs_diff,
            include_new,
            exclude_prefixes,
        )

    async def save_prices(
        self,
        records: Sequence[PriceRecord],
        *,
        observed_at: str | None = None,
    ) -> int:
        kwargs = {"observed_at": observed_at} if observed_at else {}
        return await asyncio.to_thread(
            self.repository.save_prices,
            records,
            **kwargs,
        )

    async def refresh_prices(self) -> int:
        if self.client is None:
            raise RuntimeError("Cardrush client is not configured")
        records = await asyncio.to_thread(self.client.query_all)
        if not records:
            raise RuntimeError(
                "Cardrush returned no price records; refusing to update history"
            )
        return await self.save_prices(records)

    async def reset_database(self) -> None:
        await asyncio.to_thread(self.repository.reset)
