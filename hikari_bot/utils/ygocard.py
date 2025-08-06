import json
import os
import sqlite3
import random
from PIL import Image
import aiohttp
from bs4 import BeautifulSoup
from hikari_bot.utils.constants import *

card_info_db = os.path.join(DATA_DIR, 'card_info.db')

def update_cxx():
    conn = sqlite3.connect(card_info_db)
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM cards WHERE id > 100000000")
        cursor.execute("DELETE FROM cards WHERE data NOT LIKE '%sc_name%'")
        conn.commit()
    finally:
        conn.close()

def random_card():
    cdb_path = os.path.join(YGOPRO, "cards.cdb")
    if not os.path.exists(cdb_path):
        return 0

    conn = sqlite3.connect(cdb_path)
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


def get_ygopic(id: int):
    UNKNOWN = os.path.join(YGOPRO, "textures/unknown.jpg")

    if int(id) < 100000000:
        image_path = os.path.join(YGOPRO, "pics", f"{id}.jpg")
    else:
        image_path = os.path.join(YGOPRO, "expansions/pics", f"{id}.jpg")
    
    if not os.path.exists(image_path):
        print(f"Image not found: {image_path}")
        return Image.open(UNKNOWN)

    try:
        return Image.open(image_path)
    except Exception as e:
        print(f"Error loading image {image_path}: {e}")
        return Image.open(UNKNOWN)


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
    conn = sqlite3.connect(card_info_db)
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
    if not os.path.exists(card_info_db):
        init_card_info_db()
    conn = sqlite3.connect(card_info_db)
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
                        br_tag.replace_with("\n")
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
