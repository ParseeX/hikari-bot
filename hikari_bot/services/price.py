import json
import os
import re
import sqlite3
from datetime import datetime, date
from typing import Optional, Iterable, Any

import requests

from hikari_bot.core.constants import DATA_DIR

CARD_RUSH_URL = "https://cardrush.media/yugioh/buying_prices"
DB_PATH = os.path.join(DATA_DIR, "cardrush_prices.db")

# 你现有数据库里的旧 card_prices 表，迁移时统一视为 2026-04-25 晚上 Cardrush 日终价格。
MIGRATION_TIME = "2026-04-25T22:10:00+09:00"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}


def _extract_data(html: str):
    """从 __NEXT_DATA__ 里提取 JSON"""
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )

    if not m:
        raise RuntimeError("无法找到 __NEXT_DATA__")

    return json.loads(m.group(1))


def query(name=None, rarity=None, model_number=None, limit=100):
    """实时查询 Cardrush 收购价格。"""
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
        result.append(
            {
                "name": p.get("name"),
                "price": p.get("amount"),
                "rarity": p.get("rarity"),
                "model_number": p.get("model_number"),
            }
        )

    return result


def query_all():
    return query(limit=100000)


def _now_iso() -> str:
    # 服务器如果是日本时区，这里就是 JST；如果不是，建议后续统一改成 zoneinfo。
    return datetime.now().isoformat(timespec="seconds")


def _card_key(card: dict[str, Any]) -> tuple[str, str, str]:
    return (
        card.get("name") or "",
        card.get("rarity") or "",
        card.get("model_number") or "",
    )


def init_database():
    """初始化新版价格历史数据库。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS card_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                rarity TEXT,
                model_number TEXT,
                price INTEGER NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_price_history_card_time
            ON card_price_history(name, rarity, model_number, changed_at, id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_price_history_changed_at
            ON card_price_history(changed_at)
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        conn.commit()


def _has_migrated(cursor: sqlite3.Cursor) -> bool:
    cursor.execute("SELECT value FROM schema_meta WHERE key = 'old_card_prices_migrated'")
    row = cursor.fetchone()
    return bool(row and row[0] == "1")


def migrate_old_card_prices(migration_time: str = MIGRATION_TIME) -> int:
    """
    把旧版 card_prices 表里的当前价格，迁移成一批历史记录。

    旧数据会被视为 migration_time 这个时间点的价格。
    默认是 2026-04-25T22:10:00+09:00。

    返回迁移条数。重复运行不会重复迁移。
    """
    init_database()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if _has_migrated(cursor):
            return 0

        cursor.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'card_prices'
            """
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('old_card_prices_migrated', '1')"
            )
            conn.commit()
            return 0

        cursor.execute(
            """
            SELECT name, rarity, model_number, price
            FROM card_prices
            WHERE name IS NOT NULL AND price IS NOT NULL
            """
        )
        rows = cursor.fetchall()

        inserted = 0
        for name, rarity, model_number, price in rows:
            cursor.execute(
                """
                SELECT id FROM card_price_history
                WHERE name = ?
                  AND IFNULL(rarity, '') = IFNULL(?, '')
                  AND IFNULL(model_number, '') = IFNULL(?, '')
                  AND changed_at = ?
                LIMIT 1
                """,
                (name, rarity, model_number, migration_time),
            )
            if cursor.fetchone():
                continue

            cursor.execute(
                """
                INSERT INTO card_price_history(name, rarity, model_number, price, changed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, rarity, model_number, int(price), migration_time),
            )
            inserted += 1

        cursor.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('old_card_prices_migrated', '1')"
        )
        conn.commit()
        return inserted


def get_latest_price(cursor: sqlite3.Cursor, name: str, rarity: Optional[str], model_number: Optional[str]) -> Optional[int]:
    """获取指定卡片当前最新记录价格。"""
    cursor.execute(
        """
        SELECT price
        FROM card_price_history
        WHERE name = ?
          AND IFNULL(rarity, '') = IFNULL(?, '')
          AND IFNULL(model_number, '') = IFNULL(?, '')
        ORDER BY changed_at DESC, id DESC
        LIMIT 1
        """,
        (name, rarity, model_number),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def save_prices(prices_data: list[dict[str, Any]], captured_at: Optional[str] = None) -> list[dict[str, Any]]:
    """
    保存本次抓取结果。

    只在以下情况新增历史记录：
    1. 新卡第一次出现。
    2. 最新价格和上一条历史记录不同。

    返回本次新增的变化列表。
    """
    init_database()
    migrate_old_card_prices()

    if captured_at is None:
        captured_at = _now_iso()

    changes = []

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        for card in prices_data:
            name = card.get("name")
            rarity = card.get("rarity")
            model_number = card.get("model_number")
            new_price = card.get("price")

            if not name or new_price is None:
                continue

            new_price = int(new_price)
            old_price = get_latest_price(cursor, name, rarity, model_number)

            if old_price == new_price:
                continue

            cursor.execute(
                """
                INSERT INTO card_price_history(name, rarity, model_number, price, changed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, rarity, model_number, new_price, captured_at),
            )

            changes.append(
                {
                    "name": name,
                    "rarity": rarity,
                    "model_number": model_number,
                    "old_price": old_price,
                    "new_price": new_price,
                    "change_type": "new" if old_price is None else "changed",
                    "price_diff": None if old_price is None else new_price - old_price,
                    "percent_diff": None if not old_price else (new_price - old_price) / old_price * 100,
                    "changed_at": captured_at,
                }
            )

        conn.commit()

    return changes


def get_latest_prices() -> dict[str, int]:
    """获取所有卡片的最新价格，兼容你原来的 compare_prices 用法。"""
    init_database()
    migrate_old_card_prices()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            WITH ranked AS (
                SELECT
                    name,
                    rarity,
                    model_number,
                    price,
                    ROW_NUMBER() OVER (
                        PARTITION BY name, IFNULL(rarity, ''), IFNULL(model_number, '')
                        ORDER BY changed_at DESC, id DESC
                    ) AS rn
                FROM card_price_history
            )
            SELECT name, rarity, model_number, price
            FROM ranked
            WHERE rn = 1
            """
        )

        return {
            f"{name}|{rarity or ''}|{model_number or ''}": int(price)
            for name, rarity, model_number, price in cursor.fetchall()
        }


def compare_prices(new_prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只比较，不写入数据库。"""
    old_prices = get_latest_prices()
    changes = []

    for card in new_prices:
        name = card.get("name")
        rarity = card.get("rarity")
        model_number = card.get("model_number")
        new_price = card.get("price")

        if not name or new_price is None:
            continue

        new_price = int(new_price)
        key = f"{name}|{rarity or ''}|{model_number or ''}"
        old_price = old_prices.get(key)

        if old_price is None:
            changes.append(
                {
                    "name": name,
                    "rarity": rarity,
                    "model_number": model_number,
                    "old_price": None,
                    "new_price": new_price,
                    "change_type": "new",
                }
            )
        elif old_price != new_price:
            changes.append(
                {
                    "name": name,
                    "rarity": rarity,
                    "model_number": model_number,
                    "old_price": old_price,
                    "new_price": new_price,
                    "change_type": "changed",
                    "price_diff": new_price - old_price,
                    "percent_diff": (new_price - old_price) / old_price * 100 if old_price else None,
                }
            )

    return changes


def _build_series_where(series_keywords: Optional[Iterable[str]]) -> tuple[str, list[str]]:
    if not series_keywords:
        return "", []

    keywords = [s.strip() for s in series_keywords if s and s.strip()]
    if not keywords:
        return "", []

    clauses = ["model_number LIKE ?" for _ in keywords]
    params = [f"%{s}%" for s in keywords]
    return " AND (" + " OR ".join(clauses) + ")", params


def get_daily_report_changes(
    date_str: Optional[str] = None,
    series_keywords: Optional[Iterable[str]] = None,
    min_abs_diff: int = 0,
    include_new: bool = True,
) -> list[dict[str, Any]]:
    """
    获取某一天新增的价格变化记录，可按系列编号筛选。

    date_str: '2026-04-26'。不传则默认今天。
    series_keywords: 例如 ['ALIN']，会匹配 model_number LIKE '%ALIN%'。
    min_abs_diff: 过滤小变动，例如 100 表示只看变动幅度 >= 100 円。
    include_new: 是否包含新出现的卡。
    """
    init_database()
    migrate_old_card_prices()

    if date_str is None:
        date_str = date.today().isoformat()

    series_where, series_params = _build_series_where(series_keywords)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        sql = f"""
            WITH history AS (
                SELECT
                    id,
                    name,
                    rarity,
                    model_number,
                    price AS new_price,
                    changed_at,
                    LAG(price) OVER (
                        PARTITION BY name, IFNULL(rarity, ''), IFNULL(model_number, '')
                        ORDER BY changed_at, id
                    ) AS old_price
                FROM card_price_history
            )
            SELECT
                name,
                rarity,
                model_number,
                old_price,
                new_price,
                changed_at
            FROM history
            WHERE DATE(changed_at) = DATE(?)
            {series_where}
            ORDER BY changed_at DESC, name
        """

        cursor.execute(sql, [date_str, *series_params])
        rows = cursor.fetchall()

    results = []
    for name, rarity, model_number, old_price, new_price, changed_at in rows:
        old_price = int(old_price) if old_price is not None else None
        new_price = int(new_price)

        if old_price is None:
            if not include_new:
                continue
            price_diff = None
            percent_diff = None
            change_type = "new"
        else:
            price_diff = new_price - old_price
            if abs(price_diff) < min_abs_diff:
                continue
            percent_diff = price_diff / old_price * 100 if old_price else None
            change_type = "changed"

        results.append(
            {
                "name": name,
                "rarity": rarity,
                "model_number": model_number,
                "old_price": old_price,
                "new_price": new_price,
                "change_type": change_type,
                "price_diff": price_diff,
                "percent_diff": percent_diff,
                "changed_at": changed_at,
            }
        )

    return results


def get_series_latest_prices(series_keywords: Iterable[str], limit: int = 100) -> list[dict[str, Any]]:
    """获取某个系列当前最新价格列表，适合新卡盒专题日报展示。"""
    init_database()
    migrate_old_card_prices()

    series_where, series_params = _build_series_where(series_keywords)
    if not series_where:
        raise ValueError("series_keywords 不能为空")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        sql = f"""
            WITH ranked AS (
                SELECT
                    name,
                    rarity,
                    model_number,
                    price,
                    changed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY name, IFNULL(rarity, ''), IFNULL(model_number, '')
                        ORDER BY changed_at DESC, id DESC
                    ) AS rn
                FROM card_price_history
            )
            SELECT name, rarity, model_number, price, changed_at
            FROM ranked
            WHERE rn = 1
            {series_where}
            ORDER BY price DESC
            LIMIT ?
        """
        cursor.execute(sql, [*series_params, limit])
        rows = cursor.fetchall()

    return [
        {
            "name": name,
            "rarity": rarity,
            "model_number": model_number,
            "price": int(price),
            "changed_at": changed_at,
        }
        for name, rarity, model_number, price, changed_at in rows
    ]


def split_changes(changes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """把日报变化分成新卡、上涨、下跌。"""
    new_cards = [c for c in changes if c["change_type"] == "new"]
    up = [c for c in changes if c.get("price_diff") is not None and c["price_diff"] > 0]
    down = [c for c in changes if c.get("price_diff") is not None and c["price_diff"] < 0]

    up.sort(key=lambda c: c["price_diff"], reverse=True)
    down.sort(key=lambda c: c["price_diff"])
    new_cards.sort(key=lambda c: c["new_price"], reverse=True)

    return {"new": new_cards, "up": up, "down": down}


def format_change_line(c: dict[str, Any]) -> str:
    label = f"{c['name']} [{c.get('rarity') or '-'} / {c.get('model_number') or '-'}]"
    if c["change_type"] == "new":
        return f"🆕 {label}: {c['new_price']}円"

    diff = c["price_diff"]
    sign = "+" if diff > 0 else ""
    return f"{label}: {c['old_price']}円 → {c['new_price']}円 ({sign}{diff}円)"


def build_daily_report_text(
    date_str: Optional[str] = None,
    series_keywords: Optional[Iterable[str]] = None,
    min_abs_diff: int = 0,
    top_n: int = 20,
) -> str:
    """先生成文字版日报，之后你可以把这个结果交给图片渲染模块。"""
    if date_str is None:
        date_str = date.today().isoformat()

    changes = get_daily_report_changes(
        date_str=date_str,
        series_keywords=series_keywords,
        min_abs_diff=min_abs_diff,
    )
    groups = split_changes(changes)

    title_prefix = "Cardrush 价格日报"
    if series_keywords:
        title_prefix = f"Cardrush 系列价格日报（{' / '.join(series_keywords)}）"

    lines = [f"{title_prefix} {date_str}", ""]

    if not changes:
        lines.append("今日没有符合条件的价格变化。")
        return "\n".join(lines)

    if groups["up"]:
        lines.append("🔥 上涨")
        for c in groups["up"][:top_n]:
            lines.append(format_change_line(c))
        lines.append("")

    if groups["down"]:
        lines.append("📉 下跌")
        for c in groups["down"][:top_n]:
            lines.append(format_change_line(c))
        lines.append("")

    if groups["new"]:
        lines.append("🆕 新增")
        for c in groups["new"][:top_n]:
            lines.append(format_change_line(c))
        lines.append("")

    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    migrated = migrate_old_card_prices()
    print(f"迁移旧价格记录：{migrated} 条")

    # 示例1：抓取全站并只保存价格变动。
    # cards = query_all()
    # changes = save_prices(cards)
    # print(f"本次新增变化：{len(changes)} 条")

    # 示例2：生成今日全站日报文字。
    print(build_daily_report_text())

    # 示例3：生成指定系列日报，例如 ALIN。
    # print(build_daily_report_text(series_keywords=["ALIN"], min_abs_diff=100))
