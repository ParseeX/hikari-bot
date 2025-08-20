import json
import os
import sqlite3
import random
from PIL import Image
import aiohttp
import asyncio
from io import BytesIO
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from hikari_bot.utils.constants import *



def update_db():
    conn = sqlite3.connect(YGOCDB)
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM cards WHERE id > 100000000")
        cursor.execute("DELETE FROM cards WHERE data NOT LIKE '%sc_name%'")
        conn.commit()
    finally:
        conn.close()


async def update_cdb():
    url = "https://cdn01.moecube.com/koishipro/ygopro-database/zh-CN/cards.cdb"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                with open(MOECARD_DB, "wb") as f:
                    f.write(data)
            else:
                print(f"Download failed: {resp.status}")


def metaltronus_calc(id: int):
    if not os.path.exists(MOECARD_DB):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(update_cdb())

    conn = sqlite3.connect(MOECARD_DB)
    cursor = conn.cursor()
    # 获取目标卡片信息
    cursor.execute("SELECT atk, race, attribute FROM datas WHERE id = ?", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []
    atk, race, attribute = row
    if race == 0 or attribute == 0:
        conn.close()
        return []

    # 查找所有卡片，不包含衍生物
    cursor.execute("SELECT id, atk, race, attribute FROM datas WHERE id != ? AND type NOT IN (16401, 20497)", (id,))
    id_list = []
    for cid, catk, crace, cattribute in cursor.fetchall():
        same = 0
        if atk == catk:
            same += 1
        if race == crace:
            same += 1
        if attribute == cattribute:
            same += 1
        if same >= 2:
            id_list.append(cid)

    # 根据id_list查找卡名，并按name去重，返回id
    if not id_list:
        conn.close()
        return []
    qmarks = ','.join(['?'] * len(id_list))
    cursor.execute(f"SELECT id, name FROM texts WHERE id IN ({qmarks})", id_list)
    id_name_pairs = cursor.fetchall()
    seen = set()
    result_ids = []
    # 保持原id_list顺序
    id_to_name = {id_: name for id_, name in id_name_pairs}
    for cid in id_list:
        name = id_to_name.get(cid)
        if name and name not in seen:
            seen.add(name)
            result_ids.append(cid)
    conn.close()
    return result_ids


def random_card():
    conn = sqlite3.connect(MOECARD_DB)
    cursor = conn.cursor()

    try:
        # 查询所有卡片id（也就是卡密）
        cursor.execute("SELECT id FROM texts")
        rows = cursor.fetchall()
        # 随机选择一个卡密
        random_id = random.choice(rows)[0]
        return random_id
    finally:
        conn.close()


async def get_unknown_card():
    local_path = os.path.join(CARD_PICS, f"unknown.jpg")
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read()
    
    url = f"https://cdn.233.momobako.com/ygopro/textures/unknown.jpg"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    os.makedirs(CARD_PICS, exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(data)
                    return data
                else:
                    print(f"Image not found: {url}")
                    return None
    except Exception as e:
        print(f"Error loading image {url}: {e}")
        return None


async def get_ygopic(id: int):
    local_path = os.path.join(CARD_PICS, f"{id}.jpg")
    if os.path.exists(local_path):
        with open(local_path, "rb") as f:
            return f.read()

    # 本地没有则下载
    url = f"https://cdn.233.momobako.com/ygopro/pics/{id}.jpg!half"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    os.makedirs(CARD_PICS, exist_ok=True)
                    with open(local_path, "wb") as f:
                        f.write(data)
                    return data
                else:
                    print(f"Image not found: {url}")
                    return await get_unknown_card()
    except Exception as e:
        print(f"Error loading image {url}: {e}")
        return await get_unknown_card()



async def get_image_by_id(id: int):
    image_url = IMAGE_ORIGIN + str(id) + ".jpg"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()   
                    return image_data
                else:
                    print(f"Failed to download image: {response.status}")
                    return None
        except Exception as e:
            print(f"Exception occurred while downloading image: {e}")
            return None

def keyword_in_card(card, keyword: str):
    # 如果 card 是字典，递归检查它的值
    if isinstance(card, dict):
        for key, value in card.items():
            if keyword_in_card(value, keyword):
                return True
    # 如果 card 是列表，递归检查列表的每一项
    elif isinstance(card, list):
        for item in card:
            if keyword_in_card(item, keyword):
                return True
    # 如果 card 是字符串，直接检查关键词是否在字符串中
    elif isinstance(card, str):
        if keyword.lower() in card.lower():
            return True
    # 如果 card 是其他类型，跳过
    return False

def init_card_info_db():
    conn = sqlite3.connect(YGOCDB)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()

async def get_card_info_by_id(id: str):
    if not os.path.exists(YGOCDB):
        init_card_info_db()
    conn = sqlite3.connect(YGOCDB)
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM cards WHERE id = ?", (id,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return json.loads(row[0])
    
    data = await get_card_info_by_id_from_net(id)
    
    if not data:
        conn.close()
        return None
    
    cursor.execute("""
        INSERT INTO cards (id, data)
        VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET
            data = excluded.data
    """, (id, json.dumps(data, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return data


async def get_card_info_by_id_from_net(id: str):
    url = CARD_SEARCH + id
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data["result"]
                    for result in results:
                        if abs(int(result["id"])-int(id)) <= 10:
                            return result
                    return None
                else:
                    print(f"Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            print(f"Exception occurred while fetching data: {e}")
            return None

async def get_card_info(keyword: str):
    url = CARD_SEARCH + keyword
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data["result"][0]
                    # if keyword_in_card(result, keyword):
                    #     return result
                    # else:
                    #     return None
                    return result
                else:
                    print(f"Failed to fetch data: {response.status}")
                    return None
        except Exception as e:
            print(f"Exception occurred while fetching data: {e}")
            return None
        
def is_card_id(keyword: str):
    if not keyword.isdigit():
        return False
    
    id = int(keyword)
    if id < 10000000 and id != 10000:
        return False
    if id > 99999999:
        return False
    return True

async def get_qa_by_id(id: int):
    url = FAQ + str(id)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    html = await response.text()  # 获取网页 HTML
                    soup = BeautifulSoup(html, 'html.parser')  # 使用 BeautifulSoup 解析
                    for br_tag in soup.find_all('br'):
                        br_tag.replace_with(NavigableString("\n"))
                    q_div = soup.find('div', class_='qa question')
                    a_div = soup.find('div', class_='qa answer')
                    if q_div:
                        question = q_div.get_text()
                    else:
                        question = None
                    
                    if a_div:
                        answer = a_div.get_text()
                    else:
                        answer = None

                    return question, answer
                else:
                    print(f"Failed to fetch data: {response.status}")
                    return None, None
    except Exception as e:
            print(f"Exception occurred while fetching data: {e}")
            return None, None
