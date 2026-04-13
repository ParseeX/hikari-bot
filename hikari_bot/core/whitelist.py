import json
import os

from nonebot import get_bot, get_driver
from nonebot.adapters.onebot.v11 import Bot

from hikari_bot.core.constants import DATA_DIR, ADMIN
from hikari_bot.core.logger import log_message

whitelist_file = os.path.join(DATA_DIR, 'whitelist.json')

# 内存缓存
_whitelist_cache = None

async def _load_whitelist_from_file():
    """从文件加载白名单到内存"""
    try:
        with open(whitelist_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        await log_message(f"[whitelist] File not found: {whitelist_file}")
        return {"groups": [], "users": []}
    except json.JSONDecodeError:
        await log_message(f"[whitelist] JSON decode error in file: {whitelist_file}")
        return {"groups": [], "users": []}

async def get_whitelist():
    """获取白名单（从内存缓存）"""
    global _whitelist_cache
    if _whitelist_cache is None:
        _whitelist_cache = await _load_whitelist_from_file()
    return _whitelist_cache

async def save_whitelist(whitelist):
    """保存白名单到文件并更新内存缓存"""
    global _whitelist_cache
    with open(whitelist_file, 'w', encoding='utf-8') as f:
        json.dump(whitelist, f, indent=4, ensure_ascii=False)
    _whitelist_cache = whitelist

async def add_group_to_whitelist(group_id):
    """添加群组到白名单"""
    whitelist = await get_whitelist()
    if group_id not in whitelist["groups"]:
        whitelist["groups"].append(group_id)
        await save_whitelist(whitelist)
        return True
    return False

async def is_allowed_group(group_id) -> bool:
    """检查群组是否在白名单中"""
    whitelist = await get_whitelist()
    return group_id in whitelist["groups"]
    
async def message_superusers(message: str):
    """向所有超级用户发送消息"""
    try:
        bot = get_bot()
        for uid in ADMIN:
            await bot.send_private_msg(user_id=int(uid), message=message)
    except Exception as e:
        await log_message(f"[message_superusers] Failed to send message: {e}")
