"""Cardrush 旧价格接口的兼容 facade。

新代码应直接使用 ``hikari_bot.features.cardrush`` 中的 Service、Client
和 Repository。这里保留原同步函数，避免迁移期间破坏现有调用方。
"""

from dataclasses import asdict
from datetime import date
from typing import Any, Iterable, Optional

from hikari_bot.features.cardrush import (
    PriceRecord,
    get_default_cardrush_service,
)

_service = get_default_cardrush_service()
_repository = _service.repository
_client = _service.client


def query(
    name=None,
    rarity=None,
    model_number=None,
    limit=100,
    page=None,
):
    if _client is None:
        raise RuntimeError("Cardrush client is not configured")
    return [
        record.to_mapping()
        for record in _client.query(
            name=name,
            rarity=rarity,
            model_number=model_number,
            limit=limit,
            page=page,
        )
    ]


def query_all():
    if _client is None:
        raise RuntimeError("Cardrush client is not configured")
    return [record.to_mapping() for record in _client.query_all()]


def init_database():
    _repository.initialize()


def reset_database() -> None:
    _repository.reset()


def get_latest_price(cursor, product_id):
    return _repository._get_latest_price(cursor, product_id)


def save_prices(prices_data: list[dict[str, Any] | PriceRecord]) -> int:
    records = [
        value
        if isinstance(value, PriceRecord)
        else PriceRecord.from_mapping(value)
        for value in prices_data
    ]
    return _repository.save_prices(records)


def get_daily_report_changes(
    date_str: Optional[str] = None,
    series_keywords: Optional[Iterable[str]] = None,
    min_abs_diff: int = 0,
    include_new: bool = True,
    exclude_prefixes: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    return [
        asdict(change)
        for change in _repository.get_daily_changes(
            date_str,
            series_keywords,
            min_abs_diff,
            include_new,
            exclude_prefixes,
        )
    ]


def get_series_latest_prices(
    series_keywords: Iterable[str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [
        {
            "name": value.name,
            "rarity": value.rarity,
            "model_number": value.model_number,
            "price": value.price,
            "changed_at": value.changed_at,
        }
        for value in _repository.get_series_latest(
            series_keywords,
            limit,
        )
    ]


def search_local_prices(
    name: str,
    rarity: Optional[str | list[str]] = None,
    model_number: Optional[str] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    return [
        asdict(value)
        for value in _repository.search_latest(
            name,
            rarity,
            model_number,
            limit,
        )
    ]


def get_price_history(product_id: int) -> list[dict[str, Any]]:
    return [
        asdict(value)
        for value in _repository.get_history(product_id)
    ]


def split_changes(
    changes: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    new_cards = [
        value for value in changes if value["change_type"] == "new"
    ]
    up = [
        value
        for value in changes
        if value.get("price_diff") is not None
        and value["price_diff"] > 0
    ]
    down = [
        value
        for value in changes
        if value.get("price_diff") is not None
        and value["price_diff"] < 0
    ]
    up.sort(key=lambda value: value["price_diff"], reverse=True)
    down.sort(key=lambda value: value["price_diff"])
    new_cards.sort(key=lambda value: value["new_price"], reverse=True)
    return {"new": new_cards, "up": up, "down": down}


def format_change_line(change: dict[str, Any]) -> str:
    label = (
        f"{change['name']} "
        f"[{change.get('rarity') or '-'} / "
        f"{change.get('model_number') or '-'}]"
    )
    if change["change_type"] == "new":
        return f"🆕 {label}: {change['new_price']}円"
    difference = change["price_diff"]
    sign = "+" if difference > 0 else ""
    return (
        f"{label}: {change['old_price']}円 → "
        f"{change['new_price']}円 ({sign}{difference}円)"
    )


def build_daily_report_text(
    date_str: Optional[str] = None,
    series_keywords: Optional[Iterable[str]] = None,
    min_abs_diff: int = 0,
    top_n: int = 20,
) -> str:
    if date_str is None:
        date_str = date.today().isoformat()
    changes = get_daily_report_changes(
        date_str=date_str,
        series_keywords=series_keywords,
        min_abs_diff=min_abs_diff,
    )
    groups = split_changes(changes)
    title = "Cardrush 价格日报"
    if series_keywords:
        title = (
            "Cardrush 系列价格日报"
            f"（{' / '.join(series_keywords)}）"
        )
    lines = [f"{title} {date_str}", ""]
    if not changes:
        lines.append("今日没有符合条件的价格变化。")
        return "\n".join(lines)
    for heading, key in (
        ("🔥 上涨", "up"),
        ("📉 下跌", "down"),
        ("🆕 新增", "new"),
    ):
        if not groups[key]:
            continue
        lines.append(heading)
        lines.extend(
            format_change_line(value)
            for value in groups[key][:top_n]
        )
        lines.append("")
    return "\n".join(lines).rstrip()
