import requests
import json
import re

CARD_RUSH_URL = "https://cardrush.media/yugioh/buying_prices"

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


def query(name=None, rarity=None, model_number=None):
    params = {}

    if name:
        params["name"] = name
    if rarity:
        params["rarity"] = rarity
    if model_number:
        params["model_number"] = model_number
    #params["limit"] = 20

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


if __name__ == "__main__":
    #cards = query(name="増援", rarity="シーク", model_number="RC04")
    cards = query(name="", rarity="OF", model_number="")

    for c in cards:
        print(c)