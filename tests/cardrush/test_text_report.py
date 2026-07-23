from datetime import date

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.text import (
    format_daily_report,
    parse_date_arg,
)


def change(kind: str, old: int | None, new: int) -> PriceChange:
    difference = None if old is None else new - old
    return PriceChange(
        1,
        "青眼の白龍",
        "ウルトラ",
        "QCAC-JP001",
        old,
        new,
        kind,
        difference,
        None if old in (None, 0) else difference / old * 100,
        "2026-07-23T00:00:00.000Z",
    )


def test_parse_date_arg_uses_previous_year_for_future_month_day():
    assert (
        parse_date_arg("12.31", today=date(2026, 7, 23))
        == "2025-12-31"
    )


def test_text_report_preserves_summary_and_categories():
    messages = format_daily_report(
        [
            change("changed", 1000, 1500),
            change("new", None, 2000),
        ],
        "2026-07-23",
    )

    joined = "\n".join(messages)
    assert "共 2 条变化" in joined
    assert "涨价 1" in joined
    assert "新增 1" in joined
    assert "青眼の白龍" in joined
