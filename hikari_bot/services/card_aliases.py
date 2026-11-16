"""各命令入口共用别名增删结果；权限由对应平台入口校验。"""
import asyncio
import logging
import sqlite3
from .card_catalog import AmbiguousCardName, normalize


async def add_alias_reply(catalog, argument):
    parts = argument.rsplit(maxsplit=1)
    if len(parts) != 2:
        return '用法：添加别名 卡名或卡密 别名（最后一个空格后为别名）'
    if catalog is None:
        return '卡片资料暂不可用，请稍后再试。'
    try:
        target, alias = parts
        if await asyncio.to_thread(catalog.is_card_name, alias):
            return '不能将其他卡片的完整名称设置为别名。'
        key = normalize(target)
        if not (key.isascii() and key.isdigit()):
            info = await asyncio.to_thread(catalog.search, target, fuzzy=True)
            if info is None:
                return '未找到对应卡片，请换用完整卡名或准确的卡密。'
            target = info['id']
        info, added = await asyncio.to_thread(catalog.add_alias, target, alias)
    except AmbiguousCardName as error:
        return '未添加别名。\n' + str(error)
    except ValueError as error:
        return str(error)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception('添加卡片别名失败')
        return '别名保存失败，请稍后再试。'
    name = info.get('cn_name') or info.get('jp_name') or info.get('en_name') or '未命名卡片'
    return f'已添加别名：{parts[1]} → {name}' if added else f'这个别名已关联到{name}。'


async def delete_alias_reply(catalog, argument):
    parts = argument.rsplit(maxsplit=1)
    if len(parts) != 2:
        return '用法：删除别名 完整卡名或卡密 别名（最后一个空格后为别名）'
    if catalog is None:
        return '卡片资料暂不可用，请稍后再试。'
    try:
        info, deleted = await asyncio.to_thread(catalog.delete_alias, *parts)
    except ValueError as error:
        return str(error)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception('删除卡片别名失败')
        return '别名删除失败，请稍后再试。'
    name = info.get('cn_name') or info.get('jp_name') or info.get('en_name') or '未命名卡片'
    return f'已删除别名：{parts[1]} → {name}' if deleted else f'{name}未关联这个别名。'
