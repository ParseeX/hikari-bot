import json
import os
from datetime import datetime, timedelta

import aiohttp

from hikari_bot.core.constants import DATA_DIR
from hikari_bot.core.logger import log_message

JIHUANSHE_BASE_API = "https://api.jihuanshe.com/api/"
API_NEW_TOURNAMENT = "tournaments?token="
API_TOURNAMENT = "tournaments/{id}?tournament_code={code}&token="
API_CONTESTANTS = "contestants?tournament_id={id}&page={page}&token="
API_CHECK_IN = "contestants/verify?token="
API_QUIT = "contestants/quit?token="
API_PAIRING = "battles/all?tournament_id={id}&round={round}&token="
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOjc4MDI2NSwiaXNzIjoiaHR0cDovL2FwaS5qaWh1YW5zaGUuY29tL2FwaS93ZWNoYXQvbG9naW4iLCJpYXQiOjE3NTA2NjQwODIsImV4cCI6MTc1NTg0ODA4MiwibmJmIjoxNzUwNjY0MDgyLCJqdGkiOiIzVXVzaERKTWw2clhaT1RwIn0.IX0E-qKOqKF2l9Me7NT6VomTR66erms1651qW7KC-xQ"

WINDOENT_BASE_API = "https://yugiohmatchapi.windoent.com/"
API_MATCH_SEARCH = "v1/match"
API_MATCH_INFO = "v1/match/info/"

match_state_file = os.path.join(DATA_DIR, 'match_state.json')

async def search_by_keyword(keyword: str):
    url = f"{WINDOENT_BASE_API}{API_MATCH_SEARCH}"
    params = {
        "page": 1,
        "limit": 3,
        "status": 2,
        #"type": ["2"],
        "keywords": keyword
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['data']['matchs']
                else:
                    await log_message(f"[search_by_keyword] Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            await log_message(f"[search_by_keyword] Exception occurred while fetching data: {e}")
            return None

async def get_match_detail(id: int):
    url = f"{WINDOENT_BASE_API}{API_MATCH_INFO}{id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['data']['info']
                else:
                    await log_message(f"[get_match_detail] Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            await log_message(f"[get_match_detail] Exception occurred while fetching data: {e}")
            return None
        
def get_match_state():
    try:
        with open(match_state_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    
def save_match_state(match_state):
    with open(match_state_file, 'w', encoding='utf-8') as f:
        json.dump(match_state, f, indent=4, ensure_ascii=False)

def reset_match_state(match_name, id, code):
    with open(match_state_file, 'w', encoding='utf-8') as f:
        json.dump({
            "match_name":match_name,
            "match_id": id,
            "match_code": code,
            "user_states":{},
            "checked_in": {}
            }, f, indent=4, ensure_ascii=False)

def get_next_friday():
    today = datetime.today()
    weekday = today.weekday()
    days_until_friday = (4 - weekday) % 7
    next_friday = today + timedelta(days=days_until_friday)
    return next_friday.strftime("%Y-%m-%d")

async def start_tournament(match_name):
    url = f"{JIHUANSHE_BASE_API}{API_NEW_TOURNAMENT}{TOKEN}"
    params = {
        "name": match_name,
        "started_date": get_next_friday(),
        "started_time": "19:00",
        "swiss_rounds": "5",
        "finals": "8",
        "limited_type": "ocg",
        "limited_card_date": 202410,
        "max": "64",
        "payment": "0",
        "prize": "",
        "desc": "详情加群457767939",
        "type": "online"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["tournament_code"], data["tournament_id"]
                else:
                    await log_message(f"[start_tournament] Failed to fetch data: {response.status}")
                    return None, None
        except Exception as e:
            await log_message(f"[start_tournament] Exception occurred while fetching data: {e}")
            return None, None

async def get_tournament_info(id, code):
    url = f"{JIHUANSHE_BASE_API}{API_TOURNAMENT.format(id=id,code=code)}{TOKEN}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    await log_message(f"[get_tournament_info] Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            await log_message(f"[get_tournament_info] Exception occurred while fetching data: {e}")
            return None

        
async def get_contestants(id):
    page = 1
    next_page = True
    result = []
    while next_page:
        url = f"{JIHUANSHE_BASE_API}{API_CONTESTANTS.format(id=id, page=page)}{TOKEN}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data["contestants"]["next_page_url"]:
                            page += 1
                        else:
                            next_page = False
                        users = data["contestants"]["data"]
                        for user in users:
                            user_id = user["id"]
                            user_name = user["user"]["username"]
                            result.append({"id": user_id, "name": user_name})
                    else:
                        await log_message(f"[get_contestants] Failed to fetch data: {response.status}")
            except Exception as e:
                await log_message(f"[get_contestants] Exception occurred while fetching data: {e}")
    
    return result


async def match_check_in(xcx_id):
    url = f"{JIHUANSHE_BASE_API}{API_CHECK_IN}{TOKEN}"
    params = {
        "contestant_id": xcx_id
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["message"] == "success"
                else:
                    await log_message(f"[match_check_in] Failed to fetch data: {response.status}")
                    return False
        except Exception as e:
            await log_message(f"[match_check_in] Exception occurred while fetching data: {e}")
            return False


async def match_quit(xcx_id):
    url = f"{JIHUANSHE_BASE_API}{API_QUIT}{TOKEN}"
    params = {
        "contestant_id": xcx_id
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["message"] == "success"
                else:
                    await log_message(f"[match_quit] Failed to fetch data: {response.status}")
                    return False
        except Exception as e:
            await log_message(f"[match_quit] Exception occurred while fetching data: {e}")
            return False
        
async def get_pairing(id, round):
    url = f"{JIHUANSHE_BASE_API}{API_PAIRING.format(id=id, round=round)}{TOKEN}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    result = []
                    for battle in data["battles"]:
                        result.append({"desk": battle,
                                       "a": data["battles"][battle][0]["opponent"]["username"],
                                       "b": data["battles"][battle][1]["opponent"]["username"]})
                    return result
                else:
                    await log_message(f"[get_pairing] Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            await log_message(f"[get_pairing] Exception occurred while fetching data: {e}")
            return None