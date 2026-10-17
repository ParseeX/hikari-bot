"""中译中：复用翻译插件，经过日语再翻回简体中文。"""

from nonebot import logger, on_regex, require
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.params import RegexGroup
from nonebot.rule import Rule

require("nonebot_plugin_easy_translate")

from nonebot_plugin_easy_translate import fanyi, message_check
from nonebot_plugin_easy_translate.config import pc
from nonebot_plugin_easy_translate.gtranslate.client import Translator


def _ordinary_translation(event: MessageEvent) -> bool:
    return not event.get_plaintext().startswith("中译中")


# 原插件的 x译x 正则也会命中中译中；排除它，避免重复回复。
fanyi.rule = fanyi.rule & Rule(_ordinary_translation)

roundtrip = on_regex(
    r"^中译中\s*([\s\S]*)$", rule=message_check, priority=5, block=True
)


async def translate_roundtrip(text: str) -> str:
    translator = Translator(proxies=pc.easy_translate_http_proxy)
    async with translator.client:
        japanese = await translator.translate(text, src="zh-cn", dest="ja")
        if not japanese.text.strip():
            raise ValueError("empty Japanese translation")
        chinese = await translator.translate(japanese.text, src="ja", dest="zh-cn")
        if not chinese.text.strip():
            raise ValueError("empty Chinese translation")
        return chinese.text


@roundtrip.handle()
async def handle_roundtrip(matchgroup=RegexGroup()):
    text = matchgroup[0].strip()
    if not text:
        await roundtrip.finish("用法：中译中 要翻译的话")

    try:
        result = await translate_roundtrip(text)
    except Exception as exc:
        # 不把输入文本、代理地址或上游异常原文写入日志和回复。
        logger.warning("中译中失败：{}", type(exc).__name__)
        await roundtrip.finish("翻译失败，请稍后再试。")

    await roundtrip.finish(MessageSegment.text(result))
