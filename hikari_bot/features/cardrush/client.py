import json
import re
from typing import Any

import requests

from .errors import CardrushClientError
from .models import PriceRecord


class CardrushClient:
    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        proxies: dict[str, str] | None,
        timeout: float = 30,
    ) -> None:
        self.url = url
        self.headers = headers
        self.proxies = proxies
        self.timeout = timeout

    @staticmethod
    def extract_records(html: str) -> list[PriceRecord]:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">'
            r"(.*?)</script>",
            html,
            re.DOTALL,
        )
        if not match:
            raise CardrushClientError("无法找到 __NEXT_DATA__")

        try:
            data: dict[str, Any] = json.loads(match.group(1))
            prices = data["props"]["pageProps"].get("buyingPrices", [])
            return [
                PriceRecord(
                    product_id=int(item["yugioh_ocha_product_id"]),
                    name=str(item["name"]),
                    price=int(item["amount"]),
                    rarity=item.get("rarity"),
                    model_number=item.get("model_number"),
                    updated_at=item.get("updated_at"),
                )
                for item in prices
                if item.get("yugioh_ocha_product_id")
                and item.get("name")
                and item.get("amount") is not None
            ]
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise CardrushClientError(
                f"Cardrush 页面数据解析失败: {exc}"
            ) from exc

    def query(
        self,
        name: str | None = None,
        rarity: str | None = None,
        model_number: str | None = None,
        limit: int = 100,
        page: int | None = None,
    ) -> list[PriceRecord]:
        params: dict[str, object] = {"limit": limit}
        if name:
            params["name"] = name
        if rarity:
            params["rarity"] = rarity
        if model_number:
            params["model_number"] = model_number
        if page is not None:
            params["page"] = page

        try:
            response = requests.get(
                self.url,
                params=params,
                headers=self.headers,
                proxies=self.proxies,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self.extract_records(response.text)
        except CardrushClientError:
            raise
        except requests.RequestException as exc:
            raise CardrushClientError(
                f"Cardrush request failed: {exc}"
            ) from exc

    def query_all(self) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        page = 1
        page_size = 500
        while True:
            current = self.query(limit=page_size, page=page)
            if page == 1 and not current:
                raise CardrushClientError(
                    "Cardrush returned no price records; refusing to update history"
                )
            records.extend(current)
            if len(current) < page_size:
                return records
            page += 1
