"""从桥接读取健康快照，通过现有 OneBot 连接私聊管理员。"""
import asyncio
import logging
from pathlib import Path

import httpx
from nonebot import get_bot, get_driver, require

from hikari_bot.core.config import settings
from hikari_bot.core.constants import DATA_DIR
from hikari_bot.features.card_prices.monitoring import HealthNotifications

require('nonebot_plugin_apscheduler')
from nonebot_plugin_apscheduler import scheduler

_lock = asyncio.Lock()
_monitors = {}


async def check_and_notify():
    if _lock.locked() or not settings.jihuanshe_bridge_token:
        return
    async with _lock:
        try:
            bot = get_bot()
        except ValueError:
            return
        health = None
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(
                    settings.jihuanshe_bridge_url.rstrip('/') + '/v1/health',
                    headers={'Authorization': 'Bearer ' + settings.jihuanshe_bridge_token},
                )
                response.raise_for_status()
                candidate = response.json()
                if (isinstance(candidate, dict) and candidate.get('ok') in (None, True, False)
                        and isinstance(candidate.get('checked_at'), (int, float))):
                    health = candidate
        except (httpx.HTTPError, ValueError):
            pass
        for uid in sorted(settings.superusers):
            if not uid.isdecimal():
                continue
            if uid not in _monitors:
                _monitors[uid] = HealthNotifications(Path(DATA_DIR) / 'jihuanshe-monitor' / f'{uid}.json')
            monitor = _monitors[uid]
            async def send(message, recipient=int(uid)):
                await bot.send_private_msg(user_id=recipient, message=message)
            try:
                await monitor.observe(health, send)
            except Exception as error:
                logging.warning('jihuanshe monitor notification failed: %s', type(error).__name__)


@scheduler.scheduled_job('interval', minutes=1, id='jihuanshe_health_notifications',
                         max_instances=1, coalesce=True, misfire_grace_time=60)
async def scheduled_check():
    await check_and_notify()


@get_driver().on_bot_connect
async def connected(bot):
    await check_and_notify()
