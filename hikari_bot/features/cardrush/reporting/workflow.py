from typing import Protocol

from ..models import PriceChange
from .renderer import DailyReportRenderer


class DailyChangesService(Protocol):
    async def get_daily_changes(
        self,
        date: str,
        *,
        exclude_prefixes: list[str] | None = None,
    ) -> list[PriceChange]: ...


class DailyReportWorkflow:
    def __init__(
        self,
        service: DailyChangesService,
        renderer: DailyReportRenderer,
    ) -> None:
        self.service = service
        self.renderer = renderer

    async def render_for_date(self, date: str) -> list[bytes]:
        changes = await self.service.get_daily_changes(
            date,
            exclude_prefixes=["RD/"],
        )
        if not changes:
            return []
        return await self.renderer.render(changes, date)
