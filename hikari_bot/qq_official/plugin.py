"""Only event-bound replies. No timers, broadcasts, group management or files."""
from nonebot import logger, on_message, on_notice
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent, MessageEvent
from nonebot.rule import Rule

from .commands import parse_command, reply_for
from .delivery import ReplyPager
from .preview import handle_preview
from .card_actions import CardActions
from .callbacks import CallbackController
from .editor_preview import PreviewSession
from nonebot.adapters.qq.event import InteractionCreateEvent


def register(config, *, image_loader=None, price_backend=None):
    pager = ReplyPager()
    catalog = None
    if config.catalog_path is not None:
        from hikari_bot.services.card_catalog import CardCatalog
        catalog = CardCatalog(config.catalog_path)
    cards = CardActions(catalog, image_loader=image_loader, price_backend=price_backend)

    def allowed(event: MessageEvent) -> bool:
        if isinstance(event, GroupAtMessageCreateEvent):
            return config.allows("group_at", event.get_user_id(), event.group_openid)
        if isinstance(event, C2CMessageCreateEvent):
            return config.allows("c2c", event.get_user_id())
        return False

    matcher = on_message(rule=Rule(allowed), priority=5, block=True)

    editor = PreviewSession(config, pager, catalog, image_loader=image_loader)
    from nonebot import get_driver
    try:
        get_driver().on_startup(editor.reset)
    except ValueError:
        pass  # Isolated matcher tests have no initialized driver.

    async def process(bot, event):
        text = event.get_plaintext()
        command, argument = parse_command(text)
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
        await process(callbacks.wrap(bot,event),event)

    interactions = on_notice(rule=Rule(lambda event: isinstance(event, InteractionCreateEvent)), priority=4, block=True)

    @interactions.handle()
    async def interaction(bot: Bot, event: InteractionCreateEvent):
        await callbacks.handle(bot,event)

    return matcher
