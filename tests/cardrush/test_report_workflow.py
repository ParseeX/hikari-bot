import asyncio
import shutil
from pathlib import Path

import pytest

import hikari_bot.features.cardrush.reporting.renderer as renderer_module
from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.renderer import DailyReportRenderer
from hikari_bot.features.cardrush.reporting.workflow import DailyReportWorkflow


class FakeService:
    async def get_daily_changes(self, date, **kwargs):
        return [
            PriceChange(
                1,
                "card",
                None,
                None,
                1000,
                1200,
                "changed",
                200,
                20.0,
                f"{date}T00:00:00.000Z",
            )
        ]


class FakeRenderer:
    def __init__(self):
        self.received = None

    async def render(self, changes, date):
        self.received = (changes, date)
        return [b"page-1", b"page-2"]


class FakeImageFetcher:
    async def fetch(self, changes, image_dir):
        return {}


class FakeScreenshotBackend:
    def __init__(self, error=None):
        self.error = error

    async def capture(self, html_pages, work_dir):
        if self.error:
            raise self.error
        return [b"\x89PNG\r\n\x1a\npage"]


class RecordedTemporaryDirectory:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.mkdir()
        return str(self.path)

    def __exit__(self, exc_type, exc, traceback):
        shutil.rmtree(self.path)


def sample_change():
    return PriceChange(
        1,
        "card",
        None,
        None,
        1000,
        1200,
        "changed",
        200,
        20.0,
        "2026-07-23T00:00:00.000Z",
    )


def test_workflow_queries_changes_and_returns_renderer_pages():
    renderer = FakeRenderer()
    workflow = DailyReportWorkflow(FakeService(), renderer)

    pages = asyncio.run(workflow.render_for_date("2026-07-23"))

    assert pages == [b"page-1", b"page-2"]
    assert renderer.received[1] == "2026-07-23"


def test_renderer_cleans_temporary_directory_after_success(
    tmp_path,
    monkeypatch,
):
    work_dir = tmp_path / "render"
    monkeypatch.setattr(
        renderer_module.tempfile,
        "TemporaryDirectory",
        lambda prefix: RecordedTemporaryDirectory(work_dir),
    )
    renderer = DailyReportRenderer(
        FakeImageFetcher(),
        FakeScreenshotBackend(),
    )

    pages = asyncio.run(
        renderer.render([sample_change()], "2026-07-23")
    )

    assert pages[0].startswith(b"\x89PNG")
    assert not work_dir.exists()


def test_renderer_cleans_temporary_directory_after_failure(
    tmp_path,
    monkeypatch,
):
    work_dir = tmp_path / "render"
    monkeypatch.setattr(
        renderer_module.tempfile,
        "TemporaryDirectory",
        lambda prefix: RecordedTemporaryDirectory(work_dir),
    )
    renderer = DailyReportRenderer(
        FakeImageFetcher(),
        FakeScreenshotBackend(RuntimeError("capture failed")),
    )

    with pytest.raises(CardrushRenderError, match="capture failed"):
        asyncio.run(
            renderer.render([sample_change()], "2026-07-23")
        )

    assert not work_dir.exists()
