from datetime import datetime
from io import BytesIO
from typing import Sequence

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from ..models import PricePoint


def draw_price_chart(history: Sequence[PricePoint]) -> bytes:
    """Render price history on its real timeline and return PNG bytes."""
    dates: list[datetime] = []
    prices: list[int] = []
    for record in history:
        try:
            dates.append(datetime.fromisoformat(record.changed_at))
            prices.append(record.price)
        except (TypeError, ValueError):
            continue

    if not dates:
        return b""

    figure, axes = plt.subplots(figsize=(8, 4))
    if len(dates) == 1:
        axes.scatter(dates, prices, color="#e74c3c", zorder=5, s=40)
        axes.axhline(
            y=prices[0],
            color="#e74c3c",
            linestyle="--",
            linewidth=0.8,
            alpha=0.6,
        )
    else:
        axes.plot(
            dates,
            prices,
            marker="o",
            linestyle="-",
            color="#e74c3c",
            linewidth=1.5,
            markersize=5,
            drawstyle="steps-post",
        )

    axes.xaxis.set_major_locator(mdates.AutoDateLocator())
    axes.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(axes.xaxis.get_major_locator())
    )
    figure.autofmt_xdate(rotation=30, ha="right")
    axes.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda value, _: f"{int(value):,}")
    )
    axes.set_ylabel("JPY")
    axes.annotate(
        f"{prices[0]:,}",
        (dates[0], prices[0]),
        textcoords="offset points",
        xytext=(6, 6),
        fontsize=9,
        color="#333333",
    )
    if len(prices) > 1:
        axes.annotate(
            f"{prices[-1]:,}",
            (dates[-1], prices[-1]),
            textcoords="offset points",
            xytext=(-6, -14),
            fontsize=9,
            color="#333333",
        )

    axes.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.5)
    plt.tight_layout()
    buffer = BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight", dpi=120)
    buffer.seek(0)
    plt.close(figure)
    return buffer.getvalue()
