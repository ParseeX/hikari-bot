import asyncio
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import aiohttp

from hikari_bot.services.ygocard import get_unknown_card

from ..errors import CardrushRenderError
from ..models import PriceChange
from .html import render_daily_report_html

_CARD_IMAGE_URL = (
    "https://files.cardrush.media/yugioh/ocha_products/"
    "{product_id}.webp"
)


class CardImageFetcher(Protocol):
    async def fetch(
        self,
        changes: Sequence[PriceChange],
        image_dir: Path,
    ) -> dict[int, str]: ...


class ScreenshotBackend(Protocol):
    async def capture(
        self,
        html_pages: Sequence[str],
        work_dir: Path,
    ) -> list[bytes]: ...


class AiohttpCardImageFetcher:
    def __init__(
        self,
        *,
        concurrency: int = 20,
        retries: int = 3,
        timeout: int = 10,
    ) -> None:
        self.concurrency = concurrency
        self.retries = retries
        self.timeout = timeout

    async def fetch(
        self,
        changes: Sequence[PriceChange],
        image_dir: Path,
    ) -> dict[int, str]:
        image_dir.mkdir(parents=True, exist_ok=True)
        product_ids = {
            change.product_id for change in changes if change.product_id
        }
        result: dict[int, str] = {}
        semaphore = asyncio.Semaphore(self.concurrency)

        unknown_path = image_dir / "unknown.jpg"
        if not unknown_path.exists():
            unknown_data = await get_unknown_card()
            if unknown_data:
                unknown_path.write_bytes(unknown_data)
        unknown_url = (
            unknown_path.resolve().as_uri()
            if unknown_path.exists()
            else ""
        )

        async def fetch_one(
            session: aiohttp.ClientSession,
            product_id: int,
        ) -> None:
            destination = image_dir / f"{product_id}.webp"
            if destination.exists():
                result[product_id] = destination.resolve().as_uri()
                return

            url = _CARD_IMAGE_URL.format(product_id=product_id)
            for attempt in range(self.retries):
                try:
                    async with semaphore:
                        request_timeout = aiohttp.ClientTimeout(
                            total=self.timeout
                        )
                        async with session.get(
                            url,
                            timeout=request_timeout,
                        ) as response:
                            if response.status == 200:
                                destination.write_bytes(
                                    await response.read()
                                )
                                result[product_id] = (
                                    destination.resolve().as_uri()
                                )
                                return
                except Exception:
                    if attempt < self.retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
            result[product_id] = unknown_url

        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            await asyncio.gather(
                *[
                    fetch_one(session, product_id)
                    for product_id in product_ids
                ]
            )
        return result


class PlaywrightScreenshotBackend:
    async def capture(
        self,
        html_pages: Sequence[str],
        work_dir: Path,
    ) -> list[bytes]:
        if not html_pages:
            return []

        from playwright.async_api import async_playwright

        screenshots: list[bytes] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for index, html_page in enumerate(html_pages, 1):
                    html_path = work_dir / f"page-{index}.html"
                    html_path.write_text(html_page, encoding="utf-8")
                    page = await browser.new_page(
                        viewport={"width": 1340, "height": 900}
                    )
                    try:
                        page.set_default_timeout(120_000)
                        await page.goto(
                            html_path.resolve().as_uri(),
                            wait_until="domcontentloaded",
                        )
                        await page.evaluate("document.fonts.ready")
                        screenshots.append(
                            await page.screenshot(
                                full_page=True,
                                animations="disabled",
                                timeout=120_000,
                            )
                        )
                    finally:
                        await page.close()
            finally:
                await browser.close()
        return screenshots


class DailyReportRenderer:
    def __init__(
        self,
        image_fetcher: CardImageFetcher | None = None,
        screenshot_backend: ScreenshotBackend | None = None,
    ) -> None:
        self.image_fetcher = image_fetcher or AiohttpCardImageFetcher()
        self.screenshot_backend = (
            screenshot_backend or PlaywrightScreenshotBackend()
        )

    async def render(
        self,
        changes: Sequence[PriceChange],
        date: str,
    ) -> list[bytes]:
        try:
            with tempfile.TemporaryDirectory(
                prefix="cardrush-report-"
            ) as temporary_path:
                work_dir = Path(temporary_path)
                image_dir = work_dir / "images"
                image_dir.mkdir()
                image_map = await self.image_fetcher.fetch(
                    changes,
                    image_dir,
                )
                html_pages = render_daily_report_html(
                    changes,
                    date,
                    image_map,
                )
                return await self.screenshot_backend.capture(
                    html_pages,
                    work_dir,
                )
        except CardrushRenderError:
            raise
        except Exception as error:
            raise CardrushRenderError(
                f"Cardrush report rendering failed: {error}"
            ) from error
