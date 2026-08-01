from datetime import datetime, timezone

from hikari_bot.features.cardrush.models import PricePoint
from hikari_bot.features.cardrush.reporting.chart import _chart_points


def test_chart_extends_last_price_to_current_time():
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    dates, prices = _chart_points(
        [PricePoint(1300, "2026-06-09T14:15:14.418+09:00")],
        now,
    )

    assert dates[-1] == now
    assert prices == [1300, 1300]
