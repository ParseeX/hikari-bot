"""
mycard.py — MyCard API 访问与本地数据管理服务

功能：
  - 竞技场历史战绩、玩家信息、月度排名、首胜查询（API 层）
  - QQ ↔ MyCard 用户名绑定（JSON 文件持久化）
  - 对局通知订阅管理（内存缓存 + JSON 文件持久化）
"""

import json
import os
from datetime import datetime

import aiohttp
import pytz
from nonebot import logger

from hikari_bot.core.constants import DATA_DIR


# ── 常量 ──────────────────────────────────────────────────────────────────────────────

_BASE = "https://sapi.moecube.com:444/ygopro/"

mycard_user_file      = os.path.join(DATA_DIR, "mycard_user.json")
mycard_subscribe_file = os.path.join(DATA_DIR, "subscribe.json")

_subscribe_cache: dict | None = None


# ── 内部工具 ──────────────────────────────────────────────────────────────────────────

async def _api_get(path: str, params: dict) -> dict | None:
    """向 MC API 发起 GET 请求，返回响应 JSON；失败返回 None。"""
    url = _BASE + path
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.error(f"[mycard] API {path} returned {resp.status}")
                return None
        except Exception:
            logger.exception(f"[mycard] Exception fetching {path}")
            return None


def _to_shanghai(utc_str: str) -> datetime:
    """将 UTC 字符串解析并转换为上海时区 datetime。"""
    dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.replace(tzinfo=pytz.utc).astimezone(pytz.timezone("Asia/Shanghai"))


def _read_json(path: str, default):
    """读取 JSON 文件；文件不存在或格式错误时返回 default。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: str, data) -> None:
    """将数据写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ── API 端点 ──────────────────────────────────────────────────────────────────────────

async def fetch_player_history(username: str, page_num: int = 999999):
    """获取玩家历史对战记录列表。"""
    data = await _api_get("arena/history", {"username": username, "type": 0, "page_num": page_num})
    return data.get("data", []) if data else None


async def fetch_player_info(username: str):
    """获取玩家基本信息。"""
    return await _api_get("arena/user", {"username": username})


async def fetch_player_history_rank(username: str, year: int, month: int):
    """获取玩家指定月份的历史排名。"""
    data = await _api_get("arena/historyScore", {"username": username, "season": f"{year}-{month:02}"})
    return data.get("rank") if data else None


async def fetch_latest_record(username: str):
    """获取玩家最新的一条对战记录。"""
    history = await fetch_player_history(username, page_num=1)
    return history[0] if history else None


async def is_first_win(username: str) -> bool:
    """检查用户今日是否已完成首胜。"""
    data = await _api_get("arena/firstwin", {"username": username})
    return bool(data and data.get("today") == "1")


# ── 数据处理工具 ──────────────────────────────────────────────────────────────────────

def is_specific_month(match: dict, month: int, year: int) -> bool:
    """判断对战记录是否属于指定月份（上海时区）。"""
    dt = _to_shanghai(match["start_time"])
    return dt.year == year and dt.month == month


async def mycard_get_records(player_id: str, month: int, year: int):
    """获取玩家指定月份的对战记录。"""
    history = await fetch_player_history(player_id)
    if history is None:
        return None
    return [m for m in history if is_specific_month(m, month, year)]


async def mycard_get_player_rank(player_id: str):
    """获取玩家当前竞技场排名。"""
    info = await fetch_player_info(player_id)
    return info.get("arena_rank") if info else None


# ── 本地用户绑定 ──────────────────────────────────────────────────────────────────────

def get_mycard_user() -> dict:
    """读取本地存储的 QQ 对应 MyCard 用户名绑定表。"""
    return _read_json(mycard_user_file, {})


def save_mycard_user(user_list: dict) -> None:
    """保存绑定表到文件。"""
    _write_json(mycard_user_file, user_list)


def add_mycard_user(qq: str, mycard_id: str) -> None:
    """添加或更新 QQ 与 MyCard 用户名的绑定。"""
    users = get_mycard_user()
    users[qq] = mycard_id
    save_mycard_user(users)


# ── 订阅管理 ──────────────────────────────────────────────────────────────────────────

def get_subscribe_list() -> dict:
    """读取订阅列表（内存缓存，首次访问时从文件加载）。"""
    global _subscribe_cache
    if _subscribe_cache is None:
        _subscribe_cache = _read_json(mycard_subscribe_file, {})
    return _subscribe_cache


def save_subscribe_list(subscribe_list: dict) -> None:
    """持久化订阅列表并更新内存缓存。"""
    global _subscribe_cache
    _write_json(mycard_subscribe_file, subscribe_list)
    _subscribe_cache = subscribe_list


def subscribe(usertype: str, qq: str, mycard_id: str) -> None:
    """添加订阅：将 (usertype, qq) 加入 mycard_id 的订阅者列表。"""
    subs = get_subscribe_list()
    subs.setdefault(mycard_id, [])
    if [usertype, qq] not in subs[mycard_id]:
        subs[mycard_id].append([usertype, qq])
        save_subscribe_list(subs)


def unsubscribe(usertype: str, qq: str, mycard_id: str) -> bool:
    """取消订阅；若订阅不存在则返回 False。"""
    subs = get_subscribe_list()
    entry = [usertype, qq]
    if mycard_id in subs and entry in subs[mycard_id]:
        subs[mycard_id].remove(entry)
        if not subs[mycard_id]:
            del subs[mycard_id]
        save_subscribe_list(subs)
        return True
    return False


def unsubscribe_all(usertype: str, qq: str) -> bool:
    """移除该订阅者的全部订阅；有变更则返回 True。"""
    subs = get_subscribe_list()
    entry = [usertype, qq]
    changed = False
    for mid in list(subs):
        if entry in subs[mid]:
            subs[mid].remove(entry)
            if not subs[mid]:
                del subs[mid]
            changed = True
    if changed:
        save_subscribe_list(subs)
    return changed
