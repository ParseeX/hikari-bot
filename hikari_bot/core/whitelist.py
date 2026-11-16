import asyncio

from nonebot import get_bot

from hikari_bot.core.constants import ADMIN
from hikari_bot.core.logger import log_message
from hikari_bot.persistence import get_state_store

async def get_whitelist():
    """读取白名单快照；每次查询数据库以避免多任务缓存过期。"""
    return await asyncio.to_thread(get_state_store().get_whitelist)

async def save_whitelist(whitelist):
    """整体替换白名单，保留原有 groups/users 数据结构。"""
    groups = whitelist.get("groups", [])
    users = whitelist.get("users", [])
    await asyncio.to_thread(get_state_store().replace_whitelist, groups, users)

async def add_group_to_whitelist(group_id):
    """添加群组到白名单；已存在时返回 False。"""
    return await asyncio.to_thread(get_state_store().add_group, str(group_id))

async def is_allowed_group(group_id) -> bool:
    """检查群组是否在白名单中。"""
    return await asyncio.to_thread(get_state_store().is_group_allowed, str(group_id))
    
async def message_superusers(message: str):
    """向所有超级用户发送消息"""
    try:
        from hikari_bot.services.official_publish import publish
        from hikari_bot.services.platform import Result
        await publish(Result(message))
    except Exception:
        # 队列失败不递归触发管理员通知，也不阻断旧发送通道。
        pass
    try:
        bot = get_bot()
        for uid in ADMIN:
            await bot.send_private_msg(user_id=int(uid), message=message)
    except Exception as e:
        await log_message(f"[message_superusers] Failed to send message: {e}")
