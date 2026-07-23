import re
from pathlib import Path

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.html import (
    render_daily_report_html,
)

_CSS_PATH = Path(
    "hikari_bot/features/cardrush/reporting/templates/"
    "daily_report.css"
)


def make_changes(count: int) -> list[PriceChange]:
    return [
        PriceChange(
            product_id=index,
            name=f"<card-{index}>",
            rarity="ウルトラ",
            model_number=f"TEST-JP{index:03d}",
            old_price=1000,
            new_price=1100 + index,
            change_type="changed",
            price_diff=100 + index,
            percent_diff=10.0 + index,
            changed_at="2026-07-23T00:00:00.000Z",
        )
        for index in range(1, count + 1)
    ]


def product_ids(page: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(r'data-product-id="(\d+)"', page)
    ]


def css_block(selector: str) -> str:
    css = _CSS_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^\s*{re.escape(selector)}\s*\{{([^}}]+)\}}",
        css,
        re.DOTALL | re.MULTILINE,
    )
    assert match
    return match.group(1)


def test_html_report_uses_top_25_and_35_card_detail_pages():
    pages = render_daily_report_html(
        make_changes(96),
        "2026-07-23",
        image_map={},
    )

    assert len(pages) == 4
    assert [len(product_ids(page)) for page in pages] == [
        25,
        35,
        35,
        1,
    ]
    flattened = [
        product_id
        for page in pages
        for product_id in product_ids(page)
    ]
    assert sorted(flattened) == list(range(1, 97))
    assert len(flattened) == len(set(flattened))


def test_overview_uses_compact_summary_and_escapes_names():
    page = render_daily_report_html(
        make_changes(1),
        "2026-07-23",
        image_map={},
    )[0]

    assert "统计范围" in page
    assert "500～100,000円" in page
    assert "今日异动" in page
    assert "异动 TOP 25" in page
    assert "買取価格の変動" in page
    assert "&lt;card-1&gt;" in page
    assert '<div class="overview-desc-zh">' not in page
    assert '<div class="overview-desc-ja">' not in page


def test_html_report_embeds_mobile_portrait_layout():
    page = render_daily_report_html(
        make_changes(35),
        "2026-07-23",
        image_map={},
    )[0]

    assert "width: 1080px" in page
    assert "height: 1920px" in page
    assert (
        "grid-template-columns: repeat(5, minmax(0, 1fr))"
        in page
    )
    assert "font-size: 18px" in page
    assert "font-size: 28px" in page


def test_portrait_layout_anchors_watermark_and_uses_page_card_heights():
    card = css_block(".card")
    overview_card = css_block(".grid-overview .card")
    watermark = css_block(".watermark")

    assert "height: 238px;" in card
    assert "height: 270px;" in overview_card
    assert "position: absolute;" in watermark
    assert "right: 2px;" in watermark
    assert "bottom: 0;" in watermark
    assert "margin-top:" not in watermark
