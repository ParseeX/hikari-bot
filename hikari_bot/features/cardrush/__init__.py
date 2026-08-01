"""Cardrush 价格查询核心。"""

import os
from functools import lru_cache

from hikari_bot.core.config import settings
from hikari_bot.core.constants import DATA_DIR

from .client import CardrushClient
from .errors import (
    CardrushClientError,
    CardrushError,
    CardrushRenderError,
    CardrushRepositoryError,
)
from .models import (
    DatabaseRepairResult,
    PriceChange,
    PricePoint,
    PriceRecord,
    PriceSnapshot,
)
from .repository import PriceRepository
from .service import CardrushService


@lru_cache(maxsize=1)
def get_default_cardrush_service() -> CardrushService:
    repository = PriceRepository(
        os.path.join(DATA_DIR, "cardrush_prices.db")
    )
    client = CardrushClient(
        url="https://cardrush.media/yugioh/buying_prices",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        proxies=settings.cardrush_proxies,
        timeout=settings.api_timeout,
    )
    return CardrushService(repository, client)

__all__ = [
    "CardrushClient",
    "CardrushClientError",
    "CardrushError",
    "CardrushRenderError",
    "CardrushRepositoryError",
    "CardrushService",
    "DatabaseRepairResult",
    "PriceRepository",
    "PriceChange",
    "PricePoint",
    "PriceRecord",
    "PriceSnapshot",
    "get_default_cardrush_service",
]
