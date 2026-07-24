"""
mycard.py — MyCard API 访问与本地状态管理服务

功能：
  - 竞技场历史战绩、玩家信息、月度排名、首胜查询（API 层）
  - QQ ↔ MyCard 用户名绑定（SQLite 持久化）
  - 对局通知订阅管理（SQLite 持久化）
"""

from datetime import datetime

import aiohttp
import pytz
from nonebot import logger

from hikari_bot.persistence import get_state_store


# ── 常量 ──────────────────────────────────────────────────────────────────────────────

_BASE = "https://sapi.moecube.com:444/ygopro/"

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


# ── API 端点 ──────────────────────────────────────────────────────────────────────────
# {
# 	"total": 3009,
# 	"data": [
# 		{
# 			"usernamea": "水橋パルスィ",
# 			"usernameb": "Shwijdhwiwihd",
# 			"userscorea": 2,
# 			"userscoreb": 0,
# 			"expa": 2252.5,
# 			"expb": 2556.5,
# 			"expa_ex": 2251.5,
# 			"expb_ex": 2556.5,
# 			"pta": 1607.46860743314,
# 			"ptb": 1295.16745486218,
# 			"pta_ex": 1599.46860743314,
# 			"ptb_ex": 1303.16745486218,
# 			"type": "athletic",
# 			"start_time": "2026-05-30T04:05:45.000Z",
# 			"end_time": "2026-05-30T04:14:00.000Z",
# 			"winner": "水橋パルスィ",
# 			"isfirstwin": false,
# 			"decka": null,
# 			"deckb": null
# 		}
# 	]
# }

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
    dt = _to_shanghai(match["end_time"])
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
    """读取 QQ 对应的 MyCard 用户名绑定表。"""
    return get_state_store().get_bindings()


def save_mycard_user(user_list: dict) -> None:
    """整体保存绑定表，兼容既有调用接口。"""
    get_state_store().replace_bindings(user_list)


def add_mycard_user(qq: str, mycard_id: str) -> None:
    """添加或更新 QQ 与 MyCard 用户名的绑定。"""
    users = get_mycard_user()
    users[qq] = mycard_id
    save_mycard_user(users)


# ── 订阅管理 ──────────────────────────────────────────────────────────────────────────

def get_subscribe_list() -> dict:
    """读取订阅列表，结构保持为用户名到目标列表的映射。"""
    return get_state_store().get_subscriptions()


def save_subscribe_list(subscribe_list: dict) -> None:
    """整体保存订阅列表，兼容既有调用接口。"""
    get_state_store().replace_subscriptions(subscribe_list)
    _subscribe_cache = subscribe_list


def subscribe(usertype: str, qq: str, mycard_id: str) -> None:
    """添加订阅：将 (usertype, qq) 加入 mycard_id 的订阅者列表。"""
    get_state_store().subscribe(usertype, qq, mycard_id)


def unsubscribe(usertype: str, qq: str, mycard_id: str) -> bool:
    """取消订阅；若订阅不存在则返回 False。"""
    return get_state_store().unsubscribe(usertype, qq, mycard_id)


def unsubscribe_all(usertype: str, qq: str) -> bool:
    """移除该订阅者的全部订阅；有变更则返回 True。"""
    return get_state_store().unsubscribe_all(usertype, qq)
