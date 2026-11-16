"""Explicit non-secret service profile plus systemd-managed bridge credential."""
import json
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit


def build_services(home: Path, credential_directory: Path, catalog):
    path = home / '.config/srbs/services.json'
    if not path.is_file():
        return {}  # Existing deployment remains usable before opt-in setup.
    profile = json.loads(path.read_text(encoding='utf-8'))
    if set(profile) != {'images', 'prices', 'bridge_url', 'cardrush_database'}:
        raise ValueError('invalid srbs services profile')
    if type(profile['images']) is not bool or type(profile['prices']) is not bool:
        raise ValueError('invalid srbs feature flags')
    result = {}
    if profile['images']:
        if find_spec('PIL') is None:
            raise ValueError('Pillow is required before enabling image buttons')
        from .card_actions import existing_art_loader
        result['image_loader'] = existing_art_loader
    if profile['prices']:
        url = urlsplit(profile['bridge_url'])
        if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('bridge must use an explicit loopback URL')
        database = Path(profile['cardrush_database'])
        if not database.is_absolute() or not database.is_file() or catalog is None:
            raise ValueError('read-only price database or catalog unavailable')
        # Runtime reads only the dedicated systemd credential; no production dotenv.
        token = (credential_directory / 'srbs-jhs-token').read_text(encoding='utf-8')
        if len(token) < 32 or token != token.strip() or '\x00' in token:
            raise ValueError('invalid srbs bridge credential')
        from hikari_bot.features.card_prices.client import JhsClient
        from hikari_bot.features.cardrush.read_only import ReadOnlyPrices
        from .prices import IsolatedVersionCache, PriceQueries
        reader = ReadOnlyPrices(database)
        with reader._connect() as db:
            db.execute('SELECT product_id,name,rarity,model_number,price,changed_at FROM card_price_history LIMIT 0')
        cache_path = home / '.local/share/srbs/versions.sqlite3'
        if cache_path.resolve() in {database.resolve(), Path(catalog.path).resolve()}:
            raise ValueError('price cache must be isolated')
        result['price_backend'] = PriceQueries(JhsClient(profile['bridge_url'], token, timeout=60),
            reader, IsolatedVersionCache(cache_path, catalog))
    return result
