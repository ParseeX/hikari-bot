import requests
import json
import re
import sqlite3
import os
from datetime import datetime
from hikari_bot.utils.constants import DATA_DIR

CARD_RUSH_URL = "https://cardrush.media/yugioh/buying_prices"
DB_PATH = os.path.join(DATA_DIR, "cardrush_prices.db")

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html"
}

def _extract_data(html: str):
    """从 __NEXT_DATA__ 里提取 JSON"""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL
    )

    if not m:
        raise RuntimeError("无法找到 __NEXT_DATA__")

    return json.loads(m.group(1))


def query(name=None, rarity=None, model_number=None, limit=100):
    params = {}

    if name:
        params["name"] = name
    if rarity:
        params["rarity"] = rarity
    if model_number:
        params["model_number"] = model_number
    params["limit"] = limit

    r = requests.get(CARD_RUSH_URL, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()

    data = _extract_data(r.text)

    page_props = data["props"]["pageProps"]

    prices = page_props.get("buyingPrices", [])

    result = []

    for p in prices:
        result.append({
            "name": p.get("name"),
            "price": p.get("amount"),
            "rarity": p.get("rarity"),
            "model_number": p.get("model_number"),
        })

    return result


def query_all():
    return query(limit=100000)


def init_database():
    """初始化卡价数据库"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS card_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                rarity TEXT,
                model_number TEXT,
                price INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, rarity, model_number)
            )
        ''')
        conn.commit()


def save_prices(prices_data):
    """保存价格数据到数据库"""
    init_database()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        timestamp = datetime.now().isoformat()
        
        # 清空旧数据，只保留最新的价格列表
        cursor.execute('DELETE FROM card_prices')
        
        # 插入新数据
        for card in prices_data:
            cursor.execute('''
                INSERT INTO card_prices 
                (name, rarity, model_number, price, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                card.get("name"),
                card.get("rarity"),
                card.get("model_number"),
                card.get("price"),
                timestamp
            ))
        conn.commit()


def get_latest_prices():
    """获取数据库中最新的价格数据"""
    init_database()
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, rarity, model_number, price 
            FROM card_prices
        ''')
        
        results = cursor.fetchall()
        return {
            f"{name}|{rarity or ''}|{model_number or ''}": price
            for name, rarity, model_number, price in results
        }


def compare_prices(new_prices):
    """比较新旧价格，返回有变化的卡片"""
    old_prices = get_latest_prices()
    changes = []
    
    # 创建新价格列表的键集合，用于检测删除
    new_keys = set()
    
    # 检查新卡片和价格变化
    for card in new_prices:
        name = card.get("name")
        rarity = card.get("rarity")
        model_number = card.get("model_number")
        new_price = card.get("price")
        
        if not name or new_price is None:
            continue
            
        key = f"{name}|{rarity or ''}|{model_number or ''}"
        new_keys.add(key)
        old_price = old_prices.get(key)
        
        if old_price is None:
            # 新卡片
            changes.append({
                "name": name,
                "rarity": rarity,
                "model_number": model_number,
                "old_price": None,
                "new_price": new_price,
                "change_type": "new"
            })
        elif old_price != new_price:
            # 价格有变化
            changes.append({
                "name": name,
                "rarity": rarity,
                "model_number": model_number,
                "old_price": old_price,
                "new_price": new_price,
                "change_type": "changed",
                "price_diff": new_price - old_price if old_price else 0
            })
    
    # 检查被删除的卡片（在旧价格中但不在新价格中）
    for old_key, old_price in old_prices.items():
        if old_key not in new_keys:
            # 解析键以获取卡片信息
            parts = old_key.split("|")
            name = parts[0] if parts else "未知"
            rarity = parts[1] if len(parts) > 1 and parts[1] else None
            model_number = parts[2] if len(parts) > 2 and parts[2] else None
            
            changes.append({
                "name": name,
                "rarity": rarity,
                "model_number": model_number,
                "old_price": old_price,
                "new_price": None,
                "change_type": "deleted"
            })
    
    return changes


if __name__ == "__main__":
    #cards = query(name="増援", rarity="シーク", model_number="RC04")
    cards = query(name="", rarity="OF", model_number="")

    for c in cards:
        print(c)