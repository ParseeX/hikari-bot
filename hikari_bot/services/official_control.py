"""仅由原后台执行的管理业务；官方入口不加载旧插件或启动第二份采集。"""
import asyncio
import re
from pathlib import Path

from .platform import Result

_document_task = None


async def download_document(download, album, option):
    """线程任务超时仍可能运行；保留句柄，禁止再次启动并发下载。"""
    global _document_task
    if _document_task is not None and not _document_task.done():
        return False
    if _document_task is not None:
        _document_task.exception()
    _document_task = asyncio.create_task(asyncio.to_thread(download, album, option))
    # 外层管理请求超时不能取消线程，更不能因此误以为可以重试。
    await asyncio.shield(_document_task)
    return True

CONTROL_COMMANDS = {
    '状态查询': 'status', '状态': 'status', 'status': 'status',
    '版本查询': 'version', '版本': 'version', 'version': 'version',
    '读取日志': 'logs', '日志': 'logs', 'log': 'logs',
    '添加账号': 'watch_add', '删除账号': 'watch_remove', '关注账号': 'watch_list',
    '切换通知': 'notify_toggle', 'MyCard采集状态': 'archive_status',
    'MyCard规则检查': 'archive_rules', 'MyCard文件': 'archive_file', '卡组查询': 'deck',
    '门萨': 'mensa', 'mensa': 'mensa', '切换门萨': 'mensa_toggle', '切换mensa': 'mensa_toggle',
    '卡价图报': 'price_images',
    'cardrush_test': 'price_check', '更新数据库': 'catalog_update', '随机一卡': 'random_card',
    'jm': 'document_download',
}


def redact(text, secrets=()):
    for value in secrets:
        if isinstance(value, str) and len(value) >= 4:
            text = text.replace(value, '[已隐藏]')
    text = re.sub(r'(?i)(authorization|cookie|token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[已隐藏]', text)


async def execute(action, argument):
    if action not in set(CONTROL_COMMANDS.values()):
        return Result('不支持的后台操作。')
    if len(argument) > 500 or '\x00' in argument:
        return Result('参数无效。')
    from hikari_bot.core.config import settings, PROJECT_ROOT
    from hikari_bot.plugins.monitors import mycard
    if action == 'status':
        from hikari_bot.core.logger import get_bot_startup_info
        started, uptime = get_bot_startup_info()
        return Result(f'服务器状态：\n启动时间：{started}\n运行时长：{uptime}\nMyCard监控：{"在线" if mycard.ws_status_check() else "离线"}')
    if action == 'version':
        process = await asyncio.create_subprocess_exec('git', 'log', '-1', '--format=%h %s%n%ci', cwd=PROJECT_ROOT,
                                                      stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 10)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return Result('采集后台版本：\n' + output.decode(errors='replace') if process.returncode == 0 else '后台版本查询失败。')
    if action == 'logs':
        from hikari_bot.core.logger import log_read
        lines = await log_read()
        secrets = [value for key, value in vars(settings).items() if any(s in key for s in ('token', 'password', 'secret', 'cookie'))]
        return Result(redact(''.join(lines[-30:]), secrets))
    if action in {'watch_add', 'watch_remove'}:
        return Result(await mycard.change_account(argument, add=action == 'watch_add'))
    if action == 'watch_list':
        return Result('关注账号：\n' + ('\n'.join(sorted(mycard.watched_accounts().accounts)) or '暂无'))
    if action == 'notify_toggle':
        from hikari_bot.core.feature_flags import get_notify_enabled, set_notify_enabled
        enabled = not await get_notify_enabled()
        await set_notify_enabled(enabled)
        return Result(f'已{"开启" if enabled else "关闭"}MyCard对局通知。官方投递还受个人通知开关和平台权限限制。')
    archive = mycard._archive
    if action.startswith('archive_'):
        if archive is None:
            return Result('MyCard 归档未启用。')
        if action == 'archive_status':
            return Result(await archive.status())
        if action == 'archive_rules':
            from .mycard_archive.presentation import read_rules
            rules = await asyncio.to_thread(read_rules, archive.store.rules_path)
            return Result(f'规则有效，共 {len(rules)} 条。规则在每场结束时重新读取。')
        path = await asyncio.to_thread(archive.store.bundle, argument.strip())
        if path.stat().st_size > 25 * 1024 * 1024:
            return Result('归档超过25 MiB，本次不上传；原文件仍保留。')
        return Result(file=await asyncio.to_thread(path.read_bytes), filename=f'mycard-{path.parent.name}.zip')
    if action == 'deck':
        if not argument:
            return Result('用法：卡组查询 MyCard用户名')
        from .mycard_archive.library import DeckLibrary
        from .mycard_archive.queries import player_image
        library = DeckLibrary(settings.mycard_archive_dir / 'archive.sqlite3')
        version = await asyncio.to_thread(library.latest, argument)
        if version is None:
            return Result(argument + '：暂无卡组')
        image = await player_image(argument, version, library.path.with_name('deck-rules.json'))
        return Result(images=(image,))
    if action in {'mensa', 'mensa_toggle'}:
        from hikari_bot.plugins.monitors.mensa import fetch_tokyo_slots
        if action == 'mensa_toggle':
            from hikari_bot.core.feature_flags import get_mensa_enabled, set_mensa_enabled
            from hikari_bot.plugins.monitors.mensa import scheduler, scheduled_mensa_check
            enabled = not await get_mensa_enabled()
            await set_mensa_enabled(enabled)
            if enabled and not scheduler.get_job('mensa_tokyo_monitor'):
                scheduler.add_job(scheduled_mensa_check, 'interval', minutes=3, id='mensa_tokyo_monitor', misfire_grace_time=1800)
            return Result(f'MENSA监控已{"开启" if enabled else "关闭"}。')
        slots = await fetch_tokyo_slots()
        return Result('MENSA东京考场检查结果\n' + ('\n'.join(f"{row['datetime']} - {row['status']}" for row in slots) or '暂无可报名场次'))
    if action.startswith('price_'):
        from hikari_bot.plugins.monitors.cardrush import report_workflow
        from hikari_bot.features.cardrush.reporting.text import parse_date_arg
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if action == 'price_check':
            from hikari_bot.features.cardrush.health import check_connection
            from hikari_bot.plugins.monitors.cardrush import service
            return Result(str(await check_connection(service.client)))
        today = datetime.now(ZoneInfo('Asia/Tokyo')).date()
        date = parse_date_arg(argument, today=today) if argument else today.isoformat()
        pages = await report_workflow.render_for_date(date)
        return Result(images=tuple(Path(page).read_bytes() if isinstance(page, (str, Path)) else page for page in pages)) if pages else Result('当天暂无卡价变动。')
    if action == 'catalog_update':
        from .ygocard import update_cdb
        await update_cdb()
        return Result('卡片资料库更新完成。')
    if action == 'random_card':
        from .ygocard import catalog, get_ygopic
        code = await asyncio.to_thread(catalog.random_id)
        image = await get_ygopic(code)
        return Result(images=(image,)) if image else Result('卡图暂时不可用。')
    if action == 'document_download':
        if not argument.isascii() or not argument.isdigit() or not 1 <= len(argument) <= 9 or int(argument) <= 0:
            return Result('请提供有效数字 ID。')
        from hikari_bot.plugins.jm import _create_jm_option
        from jmcomic import download_album
        if not await download_document(download_album, int(argument), _create_jm_option()):
            return Result('上一项下载仍在运行，本次未重复启动；稍后查询。')
        path = settings.jm_data_dir / f'{int(argument)}.pdf'
        if not path.is_file():
            return Result('未生成可发送的 PDF。')
        if path.stat().st_size > 25 * 1024 * 1024:
            return Result('PDF 已生成，但超过当前跨入口传输上限25 MiB；原文件保留，未上传。')
        return Result(file=await asyncio.to_thread(path.read_bytes), filename=path.name)
    return Result('此操作暂不可用。')
