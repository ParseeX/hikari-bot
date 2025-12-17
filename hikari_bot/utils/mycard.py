from datetime import datetime, timezone
import json
import os
import aiohttp
import pytz
import asyncio
from nonebot import logger
from hikari_bot.utils.constants import *

mycard_user_file = os.path.join(DATA_DIR, 'mycard_user.json')
mycard_subscribe_file = os.path.join(DATA_DIR, 'subscribe.json')

async def is_first_win(username: str) -> bool:
    """检查用户今日是否首胜"""
    url = f"{MC_BASE_API}{API_FIRST_WIN}"
    param = {
        "username": username
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=param) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("today", "0") == "1"
                else:
                    logger.exception(f"Failed to fetch data: {response.status}")
                    return False
        except Exception:
            logger.exception(f"Exception occurred while fetching data")
            return False

async def fetch_latest_record(username: str, delay: float = 0):
    """获取玩家最新的对战记录"""
    if delay > 0:
        await asyncio.sleep(delay)
    history = await fetch_player_history(username, page_num=1)
    if history:
        return history[0]
    else:
        return None

async def fetch_player_history(username: str, page_num: int = 999999):
    """
        获取玩家历史对战记录
        数据格式：
        [{
			"usernamea": "3133223461",
			"usernameb": "水橋パルスィ",
			"userscorea": 1,
			"userscoreb": 2,
			"expa": 1646,
			"expb": 1801.5,
			"expa_ex": 1645.5,
			"expb_ex": 1800.5,
			"pta": 1350.78405124773,
			"ptb": 1551.09307262336,
			"pta_ex": 1358.78405124773,
			"ptb_ex": 1543.09307262336,
			"type": "athletic",
			"start_time": "2025-12-16T22:41:34.000Z",
			"end_time": "2025-12-16T22:48:04.000Z",
			"winner": "水橋パルスィ",
			"isfirstwin": false,
			"decka": null,
			"deckb": null
		},...]
    """
    url = f"{MC_BASE_API}{API_PLAYER_HISTORY}"
    params = {
        "username": username,
        "type": 0,
        "page_num": page_num
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('data', [])
                else:
                    logger.exception(f"Failed to fetch data: {response.status}")
                    return None
        except Exception:
            logger.exception("Exception occurred while fetching data")
            return None

async def fetch_player_info(username: str):
    """获取玩家基本信息"""
    url = f"{MC_BASE_API}{API_PLAYER_INFO}"
    params = {
        "username": username
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.exception(f"Failed to fetch data: {response.status}")
                    return None
        except Exception:
            logger.exception(f"Exception occurred while fetching data")
            return None
        
async def fetch_player_history_rank(username: str, year: int, month: int):
    """获取玩家指定月份的历史排名"""
    url = f"{MC_BASE_API}{API_PLAYER_HISTORY_RANK}"
    params = {
        "username": username,
        "season": f"{year}-{month:02}"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("rank")
                else:
                    logger.exception(f"Failed to fetch data: {response.status}")
                    return None
        except Exception:
            logger.exception(f"Exception occurred while fetching data")
            return None

def is_specific_month(match, month: int, year: int):
    """判断对战记录是否属于指定月份"""
    start_time_utc = datetime.strptime(match["start_time"], "%Y-%m-%dT%H:%M:%S.%fZ")
    
    utc_zone = pytz.utc
    start_time_utc = utc_zone.localize(start_time_utc)
    start_time_bj = start_time_utc.astimezone(pytz.timezone("Asia/Shanghai"))

    return start_time_bj.year == year and start_time_bj.month == month

async def mycard_get_records(player_id: str, month: int, year: int):
    """获取玩家指定月份的对战记录"""
    history = await fetch_player_history(player_id)
    if history == None:
        return None
    
    filtered_history = [match for match in history if is_specific_month(match, month, year)]
    return filtered_history

async def mycard_get_player_rank(player_id: str):
    """获取玩家当前竞技场排名"""
    info = await fetch_player_info(player_id)
    if info == None:
        return None
    
    return info.get("arena_rank")

def get_mycard_user():
    """读取本地存储的MyCard用户列表"""
    try:
        with open(mycard_user_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    
def save_mycard_user(user_list):
    """保存MyCard用户列表到本地文件"""
    with open(mycard_user_file, 'w', encoding='utf-8') as f:
        json.dump(user_list, f, indent=4, ensure_ascii=False)

def add_mycard_user(qq, id):
    """添加QQ号与MyCard用户ID的绑定关系"""
    user_list = get_mycard_user()
    user_list[qq] = id
    save_mycard_user(user_list)


def get_subscribe_list():
    """读取本地存储的订阅列表"""
    try:
        with open(mycard_subscribe_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error("文件格式错误，无法解析")
        return {}

def save_subscribe_list(subscribe_list):
    """保存订阅列表到本地文件"""
    with open(mycard_subscribe_file, 'w', encoding='utf-8') as f:
        json.dump(subscribe_list, f, indent=4, ensure_ascii=False)

def subscribe(usertype, qq, id):
    """添加用户订阅"""
    subscribe_list = get_subscribe_list()
    if id not in subscribe_list:
        subscribe_list[id] = []
    if [usertype, qq] not in subscribe_list[id]:
        subscribe_list[id].append([usertype, qq])
        save_subscribe_list(subscribe_list)

def unsubscribe(usertype, qq, id):
    """取消用户订阅"""
    subscribe_list = get_subscribe_list()
    if id in subscribe_list:
        if [usertype, qq] in subscribe_list[id]:
            subscribe_list[id].remove([usertype, qq])
            if not subscribe_list[id]:  # Remove the ID if the list is empty
                del subscribe_list[id]
            save_subscribe_list(subscribe_list)
            return True
    return False