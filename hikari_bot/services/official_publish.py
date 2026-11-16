"""原后台只写通知队列；不从旧数字 QQ 目标推断官方收件人。"""
import asyncio
import hashlib
import json
from pathlib import Path
import time

from .official_bus import backend_bus
from .platform import Identity, Result
from .platform_state import PlatformState
from .legacy_identity import LegacyRecords
from .wording import NoticeText
from hikari_bot.qq_official.output_templates import present, Output


def subscription_targets(player=None):
    bus = backend_bus()
    if bus is None:
        return {}
    profile = Path.home() / '.config/srbs/legacy-source.json'
    legacy = LegacyRecords(json.loads(profile.read_text())['database']) if profile.exists() else None
    state = PlatformState(bus.path.parent, legacy=legacy)
    targets = {}
    for key in bus.recipients('mycard'):
        platform, app, kind, scope, opaque = json.loads(key)
        actor = Identity(platform, app, kind, opaque, scope)
        for username, status in state.subscriptions(actor):
            if player is None or player == username:
                targets.setdefault(username, []).append(key)
    return targets


async def publish(result, *, topic='admin', dedupe=None, targets=None, subscription=None):
    bus = backend_bus()
    if bus is None:
        return
    if isinstance(result.text, NoticeText):
        result = Result(present(result.text.key, result.text.values), result.images, result.file, result.filename)
    if topic == 'admin':
        from hikari_bot.core.config import settings
        from .official_control import redact
        values = [value for key, value in vars(settings).items() if any(s in key for s in ('token','secret','password','cookie'))]
        text = redact(result.text, values)
        if isinstance(result.text, Output):
            text = Output(text, result.text.key, result.text.mode)
        result = Result(text, result.images, result.file, result.filename)
    if dedupe is None:
        digest = hashlib.sha256(result.text.encode() + b''.join(result.images)).hexdigest()
        dedupe = f'{int(time.time() // 60)}:{digest}'
    await asyncio.to_thread(bus.publish, result, dedupe=dedupe, topic=topic, targets=targets, subscription=subscription)


async def mycard_notice(player, text, *, dedupe):
    try:
        targets = await asyncio.to_thread(subscription_targets, player)
        await publish(Result(text), topic='mycard', dedupe=dedupe, targets=targets.get(player, []), subscription=player)
    except Exception as exc:
        from nonebot import logger
        logger.warning(f'[official_publish] notice unavailable: {type(exc).__name__}')
