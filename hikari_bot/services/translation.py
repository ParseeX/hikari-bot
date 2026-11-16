"""复用现有翻译插件的客户端，不加载只支持 OneBot 的命令注册。"""
from importlib import import_module
from importlib.metadata import distribution
from pathlib import Path
import sys
from types import ModuleType

LANGUAGES = {'中': 'zh-cn', '繁': 'zh-tw', '英': 'en', '日': 'ja', '韩': 'ko', '法': 'fr', '俄': 'ru', '德': 'de'}


def translator_type():
    name = 'hikari_bot.services._translation_client'
    if name not in sys.modules:
        path = Path(distribution('nonebot-plugin-easy-translate').locate_file('nonebot_plugin_easy_translate/gtranslate'))
        if not (path / 'client.py').is_file():
            raise RuntimeError('翻译客户端布局已改变')
        package = ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    return import_module(name + '.client').Translator


async def translate(command, text, *, proxy=None):
    if not text:
        return '翻译/x译x [内容]；语言支持：中、繁、英、日、韩、法、俄、德'
    if len(text) > 4000:
        return '翻译内容过长，请分段发送。'
    translator = translator_type()(proxies=proxy, raise_exception=True)
    async with translator.client:
        if command == '中译中':
            japanese = await translator.translate(text, src='zh-cn', dest='ja')
            if not japanese.text.strip():
                raise ValueError('empty intermediate translation')
            result = await translator.translate(japanese.text, src='ja', dest='zh-cn')
        else:
            src, dest = ('auto', 'zh-cn') if command == '翻译' else (LANGUAGES[command[0]], LANGUAGES[command[2]])
            result = await translator.translate(text, src=src, dest=dest)
        if not result.text.strip():
            raise ValueError('empty translation')
        return result.text
