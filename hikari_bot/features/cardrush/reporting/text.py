import re
from datetime import date
from typing import Sequence

from ..models import PriceChange
from ..parsing import rarity_jp_to_en


def parse_date_arg(arg: str, *, today: date | None = None) -> str:
    """Convert M.D into an ISO date, treating future dates as last year."""
    match = re.match(r"^(\d{1,2})\.(\d{1,2})$", arg.strip())
    if not match:
        raise ValueError("日期格式不正确，请使用 M.D 格式，如 4.27")

    month, day = int(match.group(1)), int(match.group(2))
    reference_date = today or date.today()
    try:
        parsed_date = date(reference_date.year, month, day)
    except ValueError as error:
        raise ValueError(f"无效日期：{month}.{day}") from error
    if parsed_date > reference_date:
        parsed_date = date(reference_date.year - 1, month, day)
    return parsed_date.isoformat()


def _card_line(change: PriceChange, kind: str) -> str:
    box = (change.model_number or "").split("-")[0] or "?"
    rarity = rarity_jp_to_en(change.rarity or "")
    if kind == "new":
        return f"[新] {change.name} {box}-{rarity}：{change.new_price:,}円"

    old_price = change.old_price or 0
    arrow = "↑" if (change.price_diff or 0) > 0 else "↓"
    new_price = "0" if change.new_price == 0 else f"{change.new_price:,}円"
    return (
        f"{arrow} {change.name} {box}-{rarity}："
        f"{old_price:,}円 → {new_price}"
    )


def format_daily_report(
    changes: Sequence[PriceChange],
    date_str: str,
) -> list[str]:
    """Format changes as paginated text grouped by change category."""
    if not changes:
        return [f"【卡价日报 {date_str}】\n当日无价格变化记录。"]

    up: list[PriceChange] = []
    down: list[PriceChange] = []
    new: list[PriceChange] = []
    for change in changes:
        if change.change_type == "new":
            new.append(change)
        elif (change.price_diff or 0) > 0:
            up.append(change)
        else:
            down.append(change)

    page_size = 50
    messages: list[str] = []
    summary = (
        f"【卡价日报 {date_str}】共 {len(changes)} 条变化"
        f"（涨价 {len(up)} / 降价 {len(down)} / 新增 {len(new)}）"
    )
    sections = [
        ("📈 涨价", up, "up"),
        ("📉 降价/停收", down, "down"),
        ("🆕 新增", new, "new"),
    ]
    for section_title, items, kind in sections:
        if not items:
            continue
        for page_index in range(0, len(items), page_size):
            page = items[page_index : page_index + page_size]
            header_parts = [summary, f"\n{section_title}（{len(items)} 条）"]
            if len(items) > page_size:
                current = page_index // page_size + 1
                total_pages = (len(items) + page_size - 1) // page_size
                header_parts.append(f"（{current}/{total_pages}）")
            lines = ["".join(header_parts)]
            lines.extend(_card_line(change, kind) for change in page)
            messages.append("\n".join(lines))
    return messages
