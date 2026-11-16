"""Cardrush business exports; production configuration loads only on explicit use."""
from functools import lru_cache
from importlib import import_module

from .errors import CardrushClientError, CardrushError, CardrushRenderError, CardrushRepositoryError
from .models import PriceChange, PricePoint, PriceRecord, PriceSnapshot


def __getattr__(name):
    modules = {'CardrushClient': '.client', 'PriceRepository': '.repository', 'CardrushService': '.service'}
    if name in modules:
        return getattr(import_module(modules[name], __name__), name)
    raise AttributeError(name)


@lru_cache(maxsize=1)
def get_default_cardrush_service():
    import os
    from hikari_bot.core.config import settings
    from hikari_bot.core.constants import DATA_DIR
    from .client import CardrushClient
    from .repository import PriceRepository
    from .service import CardrushService
    repository = PriceRepository(os.path.join(DATA_DIR, 'cardrush_prices.db'))
    client = CardrushClient(url='https://cardrush.media/yugioh/buying_prices',
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'},
        proxies=settings.cardrush_proxies, timeout=settings.api_timeout)
    return CardrushService(repository, client)


__all__ = ['CardrushClient', 'CardrushClientError', 'CardrushError', 'CardrushRenderError',
           'CardrushRepositoryError', 'CardrushService', 'PriceRepository', 'PriceChange',
           'PricePoint', 'PriceRecord', 'PriceSnapshot', 'get_default_cardrush_service']
