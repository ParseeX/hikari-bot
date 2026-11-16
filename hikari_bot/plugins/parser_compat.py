"""Load the scoped Bilibili download/presentation compatibility adapter."""
from importlib.metadata import version

from nonebot import logger, require

require("nonebot_plugin_parser")

from nonebot_plugin_parser.download import StreamDownloader
from nonebot_plugin_parser.parsers.data import VideoContent
from nonebot_plugin_parser.renders.base import ImageRenderer

from hikari_bot.services.parser_compat import install_compat


installed = version("nonebot-plugin-parser")
if installed != "2.6.6":
    logger.warning(
        f"Bilibili compatibility adapter tested with parser 2.6.6, found {installed}; "
        "verify download/render regression tests before upgrading"
    )
install_compat(StreamDownloader, ImageRenderer, VideoContent)
logger.info("Bilibili video-only presentation and CDN Range adapter enabled")
