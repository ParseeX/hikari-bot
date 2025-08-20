from datetime import datetime
import json
import os
import aiohttp
import pytz
from hikari_bot.utils.constants import *

mycard_user_file = os.path.join(DATA_DIR, 'mycard_user.json')
mycard_subscribe_file = os.path.join(DATA_DIR, 'subscribe.json')

async def fetch_player_history(username: str):
	url = f"{MC_BASE_API}{API_PLAYER_HISTORY}"
	params = {
		"username": username,
		"type": 0,
		"page_num": 999999
	}
	async with aiohttp.ClientSession() as session:
		try:
			async with session.get(url, params=params) as response:
				if response.status == 200:
					data = await response.json()
					return data.get('data', [])
				else:
					print(f"Failed to fetch data: {response.status}")
					return None
		except Exception as e:
			print(f"Exception occurred while fetching data: {e}")
			return None

async def fetch_player_info(username: str):
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
					print(f"Failed to fetch data: {response.status}")
					return None
		except Exception as e:
			print(f"Exception occurred while fetching data: {e}")
			return None
		
async def fetch_player_history_rank(username: str, year: int, month: int):
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
					return data["rank"]
				else:
					print(f"Failed to fetch data: {response.status}")
					return None
		except Exception as e:
			print(f"Exception occurred while fetching data: {e}")
			return None

def is_specific_month(match, month: int, year: int):
	start_time_utc = datetime.strptime(match["start_time"], "%Y-%m-%dT%H:%M:%S.%fZ")
    
	utc_zone = pytz.utc
	start_time_utc = utc_zone.localize(start_time_utc)
	start_time_bj = start_time_utc.astimezone(pytz.timezone("Asia/Shanghai"))

	return start_time_bj.year == year and start_time_bj.month == month

async def mycard_get_records(player_id: str, month: int, year: int):
	history = await fetch_player_history(player_id)
	if history == None:
		return None
	
	filtered_history = [match for match in history if is_specific_month(match, month, year)]
	return filtered_history

async def mycard_get_player_rank(player_id: str):
	info = await fetch_player_info(player_id)
	if info == None:
		return None
	
	return info["arena_rank"]

def get_mycard_user():
    try:
        with open(mycard_user_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
	
def save_mycard_user(user_list):
    with open(mycard_user_file, 'w', encoding='utf-8') as f:
        json.dump(user_list, f, indent=4, ensure_ascii=False)

def add_mycard_user(qq, id):
	user_list = get_mycard_user()
	user_list[qq] = id
	save_mycard_user(user_list)



def get_subscribe_list() - > dict:
    try:
        with open(mycard_subscribe_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.error("文件格式错误，无法解析")
        return {}

def save_subscribe_list(subscribe_list):
    with open(mycard_subscribe_file, 'w', encoding='utf-8') as f:
        json.dump(subscribe_list, f, indent=4, ensure_ascii=False)

def subscribe(usertype, qq, id):
    subscribe_list = get_subscribe_list()
    if id not in subscribe_list:
        subscribe_list[id] = []
    if [usertype, qq] not in subscribe_list[id]:
        subscribe_list[id].append([usertype, qq])
        save_subscribe_list(subscribe_list)

def unsubscribe(usertype, qq, id):
    subscribe_list = get_subscribe_list()
    if id in subscribe_list:
        if [usertype, qq] in subscribe_list[id]:
            subscribe_list[id].remove([usertype, qq])
            if not subscribe_list[id]:  # 如果列表为空，删除该ID
                del subscribe_list[id]
            save_subscribe_list(subscribe_list)
            return True
    return False