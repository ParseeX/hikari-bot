"""事件绑定的查询与媒体回复；不启动旧入口后台采集，也不继承旧管理权限。"""
from .output_templates import present
from nonebot import logger, on_message, on_notice
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent, GroupMessageCreateEvent, MessageEvent
from nonebot.rule import Rule

from .commands import parse_command, reply_for, is_group_request
from .delivery import ReplyPager
from .preview import handle_preview
from .card_actions import CardActions
from .callbacks import CallbackController
from .editor_preview import PreviewSession
from nonebot.adapters.qq.event import InteractionCreateEvent
from .events import request_from_event, EventGate
from .business import Queries
from .results import ResultDelivery
from .delivery import context
from .media import NamedFileBot
import asyncio


def register(config, *, image_loader=None, price_backend=None, queries=None):
    pager = ReplyPager()
    catalog = None
    if config.catalog_path is not None:
        from hikari_bot.services.card_catalog import CardCatalog
        catalog = CardCatalog(config.catalog_path)
    cards = CardActions(catalog, image_loader=image_loader, price_backend=price_backend)
    queries = queries or Queries(catalog, image_loader=image_loader)
    results = ResultDelivery(pager)
    gate = EventGate()
    inflight = set()

    def allowed(event: MessageEvent) -> bool:
        if isinstance(event, GroupAtMessageCreateEvent):
            return config.allows("group_at", event.get_user_id(), event.group_openid)
        if isinstance(event, GroupMessageCreateEvent):
            return config.allows("group", event.get_user_id(), event.group_openid)
        if isinstance(event, C2CMessageCreateEvent):
            return config.allows("c2c", event.get_user_id())
        return False

    matcher = on_message(rule=Rule(allowed), priority=5, block=True)

    editor = PreviewSession(config, pager, catalog, image_loader=image_loader)
    from nonebot import get_driver
    try:
        get_driver().on_startup(editor.reset)
        if queries.notifications:
            driver = get_driver()
            driver.on_bot_connect(queries.notifications.connect)
            driver.on_bot_disconnect(queries.notifications.disconnect)
            driver.on_shutdown(queries.notifications.close)
            if queries.admin and queries.admin.management:
                @driver.on_bot_connect
                async def management_connected(bot):
                    if str(bot.self_id) == config.app_id:
                        queries.admin.management.bot = bot

                @driver.on_bot_disconnect
                async def management_disconnected(bot):
                    if queries.admin.management.bot is bot:
                        queries.admin.management.bot = None
    except ValueError:
        pass  # Isolated matcher tests have no initialized driver.

    async def process(bot, event):
        if str(bot.self_id) != config.app_id or not allowed(event):
            return
        request = request_from_event(bot, event)
        key = context(bot, event)
        if key is None or not pager.capacity(event, key[1]):
            return
        if isinstance(event, GroupMessageCreateEvent):
            if getattr(event.author, 'bot', False):
                return
            if not isinstance(event, GroupAtMessageCreateEvent) and not is_group_request(
                    request.text, price_active=price_backend is not None and price_backend.active(key)):
                return
        if not gate.claim(request):
            return
        if request.session in inflight:
            await bot.send(event, present('paging.plugin_e278b7fd', {}))
            return
        inflight.add(request.session)
        try:
            if queries.state is not None and not await asyncio.to_thread(queries.state.claim_event, request):
                return
            await dispatch(bot, event, request)
        except Exception:
            logger.warning('Official QQ request did not complete; no automatic retry')
        finally:
            inflight.discard(request.session)

    async def dispatch(bot, event, request):
        text = event.get_plaintext()
        command, argument = parse_command(text)
        if command == "继续" and not argument and results.has_pending(bot, event):
            await results.send(bot, event)
            return
        if command != "继续":
            results.pending.pop(context(bot, event), None)
        try:
            result = await asyncio.wait_for(queries.handle(request, command, argument), timeout=120)
            if result is not None:
                cancel_price = getattr(price_backend, 'cancel', None)
                if callable(cancel_price):
                    cancel_price(context(bot, event))
                await results.send(bot, event, result)
                return
        except Exception:
            await pager.send(bot, event, present('paging.request_failed'))
            return
        if command == "输出预览" and not argument:
            await editor.open(bot, event)
            return
        if command == "样式预览" and not argument:
            await handle_preview(config, bot, event, pager, catalog)
            return
        if await cards.handle(command, argument, config, bot, event, pager):
            return
        continuing = command == "继续" and not argument
        response = None if continuing else await reply_for(text, catalog)
        if response is None and not continuing:
            from .availability import unavailable_reply
            response = unavailable_reply(command, text)
        if continuing or response is not None:
            # SDK binds the response to event.id and increments its reply sequence.
            # Do not fall back to unsolicited delivery if a reply is rejected.
            try:
                await pager.send(bot, event, response)
            except Exception:
                logger.warning("Official QQ reply failed; no automatic retry")

    callbacks = CallbackController(config, process)

    @matcher.handle()
    async def handle(bot: Bot, event: MessageEvent):
        bot = NamedFileBot(bot)
        await process(callbacks.wrap(bot,event) if isinstance(event, C2CMessageCreateEvent) else bot,event)

    interactions = on_notice(rule=Rule(lambda event: isinstance(event, InteractionCreateEvent)), priority=4, block=True)

    @interactions.handle()
    async def interaction(bot: Bot, event: InteractionCreateEvent):
        await callbacks.handle(bot,event)

    return matcher
