"""
cr_scraper.py — 本地 Cardrush 价格爬虫，爬取后上传到服务器。

用法：
    python scripts/cr_scraper.py

环境变量（也可在脚本同目录的 .env 文件里写）：
    CR_SERVER_URL   服务器地址，例如 https://example.com
    CR_API_KEY      与服务器 CR_UPLOAD_API_KEY 一致

cron 示例（每15分钟）：
    */15 * * * * /usr/bin/python3 /path/to/scripts/cr_scraper.py >> /var/log/cr_scraper.log 2>&1
"""

import json
import logging
import os
import re
import sys

import requests

# ── 配置 ─────────────────────────────────────────────────────────────────────
CARD_RUSH_URL = "https://cardrush.media/yugioh/buying_prices"
CARD_RUSH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html",
}

SERVER_URL = os.environ.get("CR_SERVER_URL", "").rstrip("/")
API_KEY = os.environ.get("CR_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── 爬取 ─────────────────────────────────────────────────────────────────────

def _extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise RuntimeError("无法找到 __NEXT_DATA__，页面结构可能已变更")
    return json.loads(m.group(1))


def fetch_all_prices() -> list[dict]:
    log.info("开始爬取 Cardrush 价格数据…")
    r = requests.get(
        CARD_RUSH_URL,
        params={"limit": 100000},
        headers=CARD_RUSH_HEADERS,
        timeout=60,
    )
    r.raise_for_status()

    data = _extract_next_data(r.text)
    raw_prices = data["props"]["pageProps"].get("buyingPrices", [])

    result = []
    for p in raw_prices:
        pid = p.get("yugioh_ocha_product_id")
        name = p.get("name")
        price = p.get("amount")
        if not pid or not name or price is None:
            continue
        result.append(
            {
                "product_id": int(pid),
                "name": name,
                "price": int(price),
                "rarity": p.get("rarity"),
                "model_number": p.get("model_number"),
                "updated_at": p.get("updated_at"),
            }
        )

    log.info(f"爬取完成，共 {len(result)} 条价格记录")
    return result


# ── 上传 ─────────────────────────────────────────────────────────────────────

def upload_prices(prices: list[dict]) -> dict:
    if not SERVER_URL:
        raise ValueError("CR_SERVER_URL 未设置")
    if not API_KEY:
        raise ValueError("CR_API_KEY 未设置")

    endpoint = f"{SERVER_URL}"
    log.info(f"上传 {len(prices)} 条记录到 {endpoint} …")

    r = requests.post(
        endpoint,
        json={"prices": prices},
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main():
    try:
        prices = fetch_all_prices()
    except Exception as e:
        log.error(f"爬取失败：{e}")
        sys.exit(1)

    try:
        result = upload_prices(prices)
        log.info(f"上传成功：{result}")
    except Exception as e:
        log.error(f"上传失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
