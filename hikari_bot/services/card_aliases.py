"""各命令入口共用别名添加结果；权限由对应平台入口校验。"""
import asyncio
import logging
import sqlite3


async def add_alias_reply(catalog, argument):
    parts = argument.split(maxsplit=1)
    if len(parts) != 2:
        return '用法：添加别名 卡密 别名'
    if catalog is None:
        return '卡片资料暂不可用，请稍后再试。'
    try:
        info, added = await asyncio.to_thread(catalog.add_alias, *parts)
    except ValueError as error:
        return str(error)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception('添加卡片别名失败')
        return '别名保存失败，请稍后再试。'
    name = info.get('cn_name') or info.get('jp_name') or info.get('en_name') or '未命名卡片'
    return f'已添加别名：{parts[1]} → {name}' if added else f'这个别名已关联到{name}。'
