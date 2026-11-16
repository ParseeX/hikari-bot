"""功能到真实源码的索引；不把凭据、运行数据或发布器自身开放为编辑对象。"""
from pathlib import Path

Q = 'hikari_bot/qq_official/'
S = 'hikari_bot/services/'
F = 'hikari_bot/features/'
P = 'hikari_bot/plugins/'

SOURCES = {
    'binding': [Q+'business.py', S+'platform_state.py'],
    'history': [S+'mycard_reports.py', S+'mycard_history.py', S+'mycard.py', Q+'business.py'],
    'score': [Q+'business.py', S+'mycard.py'],
    'winrate': [S+'mycard_reports.py', S+'mycard.py'],
    'passcode': [Q+'commands.py', S+'card_catalog.py'],
    'effect': [Q+'card_actions.py', Q+'commands.py', S+'card_catalog.py'],
    'art': [Q+'card_actions.py', S+'card_media.py', S+'card_image_sources.py'],
    'daily_card': [Q+'business.py', S+'card_media.py'],
    'calculation': [Q+'business.py', S+'card_media.py'],
    'price': [Q+'price_outputs.py', Q+'prices.py', F+'card_prices/service.py', F+'card_prices/japanese.py'],
    'price_history': [S+'price_reports.py', F+'cardrush/reporting/chart.py'],
    'price_daily': [F+'cardrush/reporting/text.py', S+'price_reports.py', P+'monitors/cardrush.py', F+'cardrush/reporting/renderer.py'],
    'subscriptions': [Q+'business.py', P+'monitors/mycard.py', S+'official_publish.py'],
    'notifications': [Q+'notifications.py', S+'official_publish.py'],
    'opponent': [S+'mycard_archive/opponent_watch.py', S+'official_control.py', P+'monitors/mycard.py'],
    'ruling': [S+'ruling_assistant.py', S+'ruling_client.py', Q+'business.py'],
    'deck': [S+'deck_renderer.py', S+'deck_png.py', S+'deck_message_image.py', Q+'business.py'],
    'archive': [S+'official_control.py', S+'mycard_archive/library.py', S+'mycard_archive/queries.py'],
    'front_decks': [Q+'availability.py', S+'mycard_archive/front_stats.py'],
    'links': [S+'link_parser.py', S+'link_worker.py', S+'parser_compat.py', Q+'business.py'],
    'translation': [S+'translation.py', Q+'business.py'],
    'operations': [Q+'admin.py', S+'official_control.py', Q+'management.py'],
    'monitor': [S+'mycard_archive/runtime.py', S+'official_control.py'],
    'maintenance': [Q+'management.py'],
    'power': [Q+'admin.py', S+'power.py'],
    'mensa': [S+'official_control.py', P+'monitors/mensa.py'],
    'document': [S+'official_control.py', P+'jm.py'],
    'broadcast': [Q+'management.py', S+'official_publish.py'],
    'members': [Q+'management.py'],
    'common': [Q+'commands.py', Q+'plugin.py'],
    'paging': [Q+'results.py', Q+'delivery.py', Q+'plugin.py'],
    'preview': [Q+'preview.py', Q+'editor_preview.py'],
    'callback': [Q+'callbacks.py', Q+'card_actions.py'],
    'tags': [Q+'availability.py'],
    'attachments': [Q+'business.py'],
    'ai': [Q+'management.py'],
    'price_reset': [Q+'management.py'],
    'whitelist': [Q+'management.py'],
    'background': [P+'monitors/cardrush.py', P+'monitors/mycard.py'],
}
SCHEMA_PATH = Q+'output_schema.json'
ALLOWED = frozenset({p for rows in SOURCES.values() for p in rows} | {SCHEMA_PATH})


def files(feature):
    if feature not in SOURCES:
        return []
    return [*SOURCES[feature], SCHEMA_PATH]


def checked_path(root, name):
    if name not in ALLOWED:
        raise ValueError('该文件不在功能源码目录中')
    root = Path(root).resolve(strict=True)
    path = root/name
    if any(p.is_symlink() for p in [path, *path.parents] if p != root and root in p.parents):
        raise ValueError('源码路径不能是符号链接')
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('源码文件不存在')
    if path.stat().st_size > 1_000_000:
        raise ValueError('源码文件超过编辑上限')
    return path
