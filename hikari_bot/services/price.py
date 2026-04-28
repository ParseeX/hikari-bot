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
                "product_id": p.get("yugioh_ocha_product_id"),
                "name": p.get("name"),
                "price": p.get("amount"),
                "rarity": p.get("rarity"),
                "model_number": p.get("model_number"),
                "updated_at": p.get("updated_at"),
            }
        )

    return result


def query_all():
    return query(limit=100000)


def init_database():
    """初始化新版价格历史数据库。"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS card_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
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
            CREATE INDEX IF NOT EXISTS idx_card_price_history_product_time
            ON card_price_history(product_id, changed_at, id)
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_card_price_history_changed_at
            ON card_price_history(changed_at)
            """
        )

        conn.commit()


def reset_database() -> None:
    """清空并重建数据库（架构变更后首次重建用）。"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS card_price_history")
        conn.commit()
    init_database()


def get_latest_price(cursor: sqlite3.Cursor, product_id: int) -> Optional[tuple[int, str]]:
    """获取指定 product_id 的最新价格及更新时间，返回 (price, changed_at) 或 None。"""
    cursor.execute(
        """
        SELECT price, changed_at
        FROM card_price_history
        WHERE product_id = ?
        ORDER BY changed_at DESC, id DESC
        LIMIT 1
        """,
        (product_id,),
    )
    row = cursor.fetchone()
    return (int(row[0]), row[1]) if row else None


def save_prices(prices_data: list[dict[str, Any]]) -> int:
    """
    保存本次抓取结果。

    只在以下情况新增历史记录：
    1. 新卡第一次出现。
    2. 最新价格和上一条历史记录不同。
    3. 本次响应中消失（不再收购）的卡，写入 price=0 记录。

    changed_at 使用 API 返回的 updated_at（消失的卡用当前时间）。
    返回本次新增的记录数量。
    """
    init_database()

    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    seen_product_ids: set[int] = set()
    count = 0

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # ── 处理 API 返回的卡 ──────────────────────────────────────────────
        for card in prices_data:
            product_id = card.get("product_id")
            name = card.get("name")
            rarity = card.get("rarity")
            model_number = card.get("model_number")
            new_price = card.get("price")
            updated_at = card.get("updated_at")

            if not product_id or not name or new_price is None:
                continue

            product_id = int(product_id)
            new_price = int(new_price)
            seen_product_ids.add(product_id)

            latest = get_latest_price(cursor, product_id)
            old_price = latest[0] if latest else None

            if old_price == new_price:
                continue

            cursor.execute(
                """
                INSERT INTO card_price_history(product_id, name, rarity, model_number, price, changed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (product_id, name, rarity, model_number, new_price, updated_at),
            )
            count += 1

        # ── 检测消失的卡（不再收购 → 写入 price=0）────────────────────────
        # 取出所有最新价格不为 0 的卡（即还"在收"的卡）
        cursor.execute(
            """
            WITH ranked AS (
                SELECT product_id, name, rarity, model_number, price,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_id ORDER BY changed_at DESC, id DESC
                       ) AS rn
                FROM card_price_history
            )
            SELECT product_id, name, rarity, model_number
            FROM ranked
            WHERE rn = 1 AND price != 0
            """
        )
        active_cards = cursor.fetchall()

        for product_id, name, rarity, model_number in active_cards:
            if product_id not in seen_product_ids:
                cursor.execute(
                    """
                    INSERT INTO card_price_history(product_id, name, rarity, model_number, price, changed_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (product_id, name, rarity, model_number, now_str),
                )
                count += 1

        conn.commit()

    return count


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
    exclude_prefixes: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """
    获取某一天新增的价格变化记录，可按系列编号筛选。

    date_str: '2026-04-26'。不传则默认今天。
    series_keywords: 例如 ['ALIN']，会匹配 model_number LIKE '%ALIN%'。
    min_abs_diff: 过滤小变动，例如 100 表示只看变动幅度 >= 100 円。
    include_new: 是否包含新出现的卡。
    exclude_prefixes: 排除 model_number 以这些前缀开头的卡，例如 ['RD/']。
    """
    init_database()

    if date_str is None:
        date_str = date.today().isoformat()

    series_where, series_params = _build_series_where(series_keywords)

    # 构造排除前缀条件
    exclude_list = [p for p in (exclude_prefixes or []) if p]
    if exclude_list:
        exclude_clauses = " AND ".join("(model_number IS NULL OR model_number NOT LIKE ?)" for _ in exclude_list)
        exclude_where = f" AND ({exclude_clauses})"
        exclude_params = [f"{p}%" for p in exclude_list]
    else:
        exclude_where = ""
        exclude_params = []

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        sql = f"""
            WITH history AS (
                SELECT
                    id,
                    product_id,
                    name,
                    rarity,
                    model_number,
                    price AS new_price,
                    changed_at,
                    LAG(price) OVER (
                        PARTITION BY product_id
                        ORDER BY changed_at, id
                    ) AS old_price
                FROM card_price_history
            )
            SELECT
                product_id,
                name,
                rarity,
                model_number,
                old_price,
                new_price,
                changed_at
            FROM history
            WHERE DATE(changed_at) = DATE(?)
            {series_where}
            {exclude_where}
            ORDER BY product_id DESC, name
        """

        cursor.execute(sql, [date_str, *series_params, *exclude_params])
        rows = cursor.fetchall()

    results = []
    for product_id, name, rarity, model_number, old_price, new_price, changed_at in rows:
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
                "product_id": product_id,
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
                        PARTITION BY product_id
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


def search_local_prices(
    name: str,
    rarity: Optional[str | list[str]] = None,
    model_number: Optional[str] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    在本地数据库中按卡名（模糊）、稀有度、型号前缀（模糊）查询最新价格。

    name: 支持模糊匹配（LIKE %name%）。
    rarity: 日文稀有度名称，str 则精确匹配，list 则 IN 匹配（支持前缀展开）。
    model_number: 模糊匹配（LIKE %model_number%），支持只输入盒子编号如 "ALIN"。
    返回列表按价格倒序，包含 product_id / name / rarity / model_number / price / changed_at。
    """
    init_database()

    conditions = ["name LIKE ?"]
    params: list[Any] = [f"%{name}%"]

    if rarity is not None:
        if isinstance(rarity, list):
            if rarity:
                placeholders = ",".join("?" * len(rarity))
                conditions.append(f"rarity IN ({placeholders})")
                params.extend(rarity)
            # 空列表意味着没有任何日文名匹配，结果必为空，加个永假条件
            else:
                conditions.append("0")
        else:
            conditions.append("IFNULL(rarity, '') = IFNULL(?, '')")
            params.append(rarity)
    if model_number is not None:
        conditions.append("model_number LIKE ?")
        params.append(f"%{model_number}%")

    where = " AND ".join(conditions)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        sql = f"""
            WITH ranked AS (
                SELECT
                    product_id,
                    name,
                    rarity,
                    model_number,
                    price,
                    changed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY product_id
                        ORDER BY changed_at DESC, id DESC
                    ) AS rn
                FROM card_price_history
                WHERE {where}
            )
            SELECT product_id, name, rarity, model_number, price, changed_at
            FROM ranked
            WHERE rn = 1
            ORDER BY price DESC
            LIMIT ?
        """
        cursor.execute(sql, [*params, limit])
        rows = cursor.fetchall()

    return [
        {
            "product_id": product_id,
            "name": name,
            "rarity": rarity,
            "model_number": model_number,
            "price": int(price),
            "changed_at": changed_at,
        }
        for product_id, name, rarity, model_number, price, changed_at in rows
    ]


def get_price_history(product_id: int) -> list[dict[str, Any]]:
    """获取指定 product_id 的完整价格历史（按时间升序）。"""
    init_database()
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT price, changed_at
            FROM card_price_history
            WHERE product_id = ?
            ORDER BY changed_at ASC, id ASC
            """,
            (product_id,),
        )
        rows = cursor.fetchall()
    return [{"price": int(price), "changed_at": changed_at} for price, changed_at in rows]


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

