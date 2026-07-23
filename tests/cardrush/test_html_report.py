from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.html import (
    render_daily_report_html,
)


def test_html_report_paginates_and_escapes_card_names():
    changes = [
        PriceChange(
            product_id=index,
            name=f"<card-{index}>",
            rarity="ウルトラ",
            model_number="TEST-JP001",
            old_price=1000,
            new_price=1100 + index,
            change_type="changed",
            price_diff=100 + index,
            percent_diff=10.0,
            changed_at="2026-07-23T00:00:00.000Z",
        )
        for index in range(1, 48)
    ]

    pages = render_daily_report_html(
        changes,
        "2026-07-23",
        image_map={},
    )

    assert len(pages) >= 2
    assert "&lt;card-1&gt;" in "\n".join(pages)
    assert "PAGE 1/" in pages[0]
