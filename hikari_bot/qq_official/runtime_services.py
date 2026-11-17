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


def build_migration_queries(home, catalog, *, price_backend=None, config=None, credential_directory=None):
    """独立状态及缓存；显式配置后仅只读访问已关联的旧业务记录。"""
    from functools import partial
    from hikari_bot.services.platform_state import PlatformState
    from hikari_bot.services.card_media import CardMedia
    from hikari_bot.services.deck_renderer import DeckRenderer
    from hikari_bot.services.ruling_client import conclusion
    from hikari_bot.services.link_parser import LinkParser
    from hikari_bot.services.price_reports import PriceReports
    from .business import Queries
    from .admin import AdminQueries
    directory = Path(home) / '.local/share/srbs/migration'
    legacy = None
    legacy_profile = Path(home) / '.config/srbs/legacy-source.json'
    if legacy_profile.is_file():
        from hikari_bot.services.legacy_identity import LegacyRecords
        profile = json.loads(legacy_profile.read_text(encoding='utf-8'))
        if set(profile) != {'database'}:
            raise ValueError('invalid legacy source profile')
        legacy = LegacyRecords(profile['database'])
    media = CardMedia(catalog, directory / 'card-images') if catalog else None
    power = None
    power_profile = Path(home) / '.config/srbs/nas-power.json'
    if config and config.admins and credential_directory and power_profile.is_file():
        from hikari_bot.services.power import request_action
        profile = json.loads(power_profile.read_text(encoding='utf-8'))
        if set(profile) != {'base_url', 'timeout'} or not isinstance(profile['base_url'], str):
            raise ValueError('invalid NAS service profile')
        url = urlsplit(profile['base_url'])
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('invalid NAS service URL')
        if type(profile['timeout']) not in {int, float} or not 1 <= profile['timeout'] <= 30:
            raise ValueError('invalid NAS service timeout')
        token = (Path(credential_directory) / 'srbs-nas-token').read_text(encoding='utf-8')
        if not token or token != token.strip() or '\x00' in token:
            raise ValueError('invalid NAS credential')
        power = partial(request_action, profile['base_url'], token, timeout=profile['timeout'])
    from hikari_bot.services.official_bus import OfficialBus
    from .notifications import Notifications
    from .management import Management
    bus = OfficialBus(directory)
    state = PlatformState(directory, legacy=legacy)
    notifications = None
    if config:
        bus.configure(config)
        policy_path = Path(home) / '.config/srbs/notifications.json'
        policy = json.loads(policy_path.read_text()) if policy_path.is_file() else {'enabled': False, 'daily_limit': 20}
        if set(policy) != {'enabled', 'daily_limit'} or type(policy['enabled']) is not bool or type(policy['daily_limit']) is not int or not 1 <= policy['daily_limit'] <= 100:
            raise ValueError('invalid notification policy')
        notifications = Notifications(bus, state, config, **policy)
    async def restart():
        import asyncio
        process = await asyncio.create_subprocess_exec('systemctl', '--user', 'restart', 'srbs.service',
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()

    management = Management(config, bus, restart=restart if credential_directory else None) if config else None
    return Queries(catalog, image_loader=media.full if media else None,
                   deck_renderer=DeckRenderer(catalog, media.full) if media else None,
                   ruling=partial(conclusion, url='https://ocg-ruling-assistant.vercel.app/api/answer'),
                   state=state, links=LinkParser(), notifications=notifications,
                   price_reports=PriceReports(price_backend.comparison.cardrush, catalog, selection_limit=100) if price_backend else None,
                   admin=AdminQueries(config, power=power, bus=bus, management=management) if config else None)
