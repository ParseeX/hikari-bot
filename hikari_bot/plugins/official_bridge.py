"""原后台唯一的官方管理队列消费者，默认未配置时不启动。"""
import asyncio
import json
from nonebot import get_driver, logger

from hikari_bot.services.official_bus import backend_bus
from hikari_bot.services.official_control import execute
from hikari_bot.services.platform import Result
from hikari_bot.qq_official.output_templates import present

_task = None
_heartbeat_task = None


async def heartbeat(bus):
    while True:
        try:
            await asyncio.to_thread(bus.heartbeat)
        except Exception as exc:
            logger.warning(f'[official_bridge] heartbeat error: {type(exc).__name__}')
        await asyncio.sleep(2)


async def run(bus):
    while True:
        try:
            job = await asyncio.to_thread(bus.claim, 'control')
            if job is None:
                await asyncio.sleep(2)
                continue
            request = json.loads(job['body'])
            try:
                result = await asyncio.wait_for(execute(request['action'], request['argument']), timeout=110)
            except Exception as exc:
                logger.warning(f'[official_bridge] operation failed: {type(exc).__name__}')
                result = Result(present('operations.backend_failed'))
            await asyncio.to_thread(bus.finish, job['id'], 'completed', result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'[official_bridge] queue error: {type(exc).__name__}')
            await asyncio.sleep(5)


@get_driver().on_startup
async def start():
    global _task, _heartbeat_task
    bus = backend_bus()
    if bus and (_task is None or _task.done()):
        _task = asyncio.create_task(run(bus))
        _heartbeat_task = asyncio.create_task(heartbeat(bus))


@get_driver().on_shutdown
async def stop():
    global _task, _heartbeat_task
    tasks = [task for task in (_task, _heartbeat_task) if task]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _task = _heartbeat_task = None
