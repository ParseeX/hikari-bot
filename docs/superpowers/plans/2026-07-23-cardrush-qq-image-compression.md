# Cardrush QQ Image Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变原始报表 PNG 和 B 站输入的前提下，把每一页 QQ 图报自适应压缩为不超过 1,000,000 字节的 JPEG。

**Architecture:** 在 Cardrush 报表包中新增纯 Pillow 压缩函数，在 QQ 适配层旁新增异步页面准备器，通过线程执行 CPU 密集型压缩并记录大小。手动图报和自动管理员日报只发送压缩副本，B 站入口继续使用工作流返回的原始 PNG。

**Tech Stack:** Python 3.10+、Pillow、asyncio、NoneBot 2、pytest、pyflakes、Playwright。

## Global Constraints

- QQ 单页图片的试行上限为 `1_000_000` 字节；该值不是 QQ 官方限制。
- 输出格式为 JPEG，质量从 85 开始，以 5 为步长最低降至 40。
- 质量 40 仍超限时按 85% 等比缩小，最小宽度为 640 像素。
- 手动“卡价图报”和自动 QQ 日报使用压缩副本。
- B 站发布及其他消费者继续使用原始 PNG。
- 不增加 `retcode=1200` 自动重试。
- 不改变 HTML、CSS、Playwright 布局、命令、权限、调度时间或消息文案。
- 每个生产代码步骤必须先有一个因目标接口缺失或行为不满足而失败的测试。

## File Map

**Create**

- `hikari_bot/features/cardrush/reporting/compression.py`：纯 Pillow 自适应 JPEG 压缩。
- `hikari_bot/plugins/monitors/cardrush_delivery.py`：异步准备 QQ 页面并记录压缩大小。
- `tests/cardrush/test_compression.py`：压缩大小、格式、宽高比和错误契约。
- `tests/cardrush/test_qq_delivery.py`：页面只压缩一次及日志契约。

**Modify**

- `hikari_bot/features/cardrush/reporting/__init__.py`：导出 `compress_for_qq`。
- `hikari_bot/plugins/monitors/cardrush.py`：手动与自动 QQ 发送改用压缩副本。
- `tests/cardrush/test_plugin_import.py`：锁定两条 QQ 路径和 B 站原图路径。

---

### Task 1: 实现可测试的自适应 JPEG 压缩

**Files:**

- Create: `hikari_bot/features/cardrush/reporting/compression.py`
- Create: `tests/cardrush/test_compression.py`
- Modify: `hikari_bot/features/cardrush/reporting/__init__.py`

**Interfaces:**

- Consumes: Pillow `Image`、`CardrushRenderError`
- Produces: `compress_for_qq(image_bytes: bytes, max_bytes: int = 1_000_000) -> bytes`

- [ ] **Step 1: 写入失败的压缩契约测试**

创建 `tests/cardrush/test_compression.py`：

```python
from io import BytesIO

import pytest
from PIL import Image

from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.reporting.compression import (
    compress_for_qq,
)


def noisy_png() -> bytes:
    image = Image.effect_noise((1800, 1200), 100).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compress_for_qq_returns_jpeg_within_limit_and_keeps_ratio():
    source = noisy_png()

    result = compress_for_qq(source, max_bytes=200_000)

    assert len(result) <= 200_000
    assert result.startswith(b"\xff\xd8")
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.width / image.height == pytest.approx(
            1800 / 1200,
            rel=0.01,
        )


def test_compress_for_qq_rejects_invalid_image():
    with pytest.raises(CardrushRenderError, match="decode"):
        compress_for_qq(b"not-an-image")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_compression.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'hikari_bot.features.cardrush.reporting.compression'`。

- [ ] **Step 3: 实现最小压缩算法**

创建 `hikari_bot/features/cardrush/reporting/compression.py`：

```python
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 85
_MIN_QUALITY = 40
_QUALITY_STEP = 5
_RESIZE_FACTOR = 0.85
_MIN_WIDTH = 640


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )
    return buffer.getvalue()


def compress_for_qq(
    image_bytes: bytes,
    max_bytes: int = 1_000_000,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            working = source.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise CardrushRenderError(
            f"Unable to decode QQ report image: {error}"
        ) from error

    while True:
        for quality in range(
            _MAX_QUALITY,
            _MIN_QUALITY - 1,
            -_QUALITY_STEP,
        ):
            encoded = _encode_jpeg(working, quality)
            if len(encoded) <= max_bytes:
                return encoded

        if working.width <= _MIN_WIDTH:
            break
        next_width = max(
            _MIN_WIDTH,
            int(working.width * _RESIZE_FACTOR),
        )
        next_height = max(
            1,
            round(working.height * next_width / working.width),
        )
        working = working.resize(
            (next_width, next_height),
            Image.Resampling.LANCZOS,
        )

    raise CardrushRenderError(
        "Unable to compress QQ report image below "
        f"{max_bytes} bytes"
    )
```

更新 `hikari_bot/features/cardrush/reporting/__init__.py`：

```python
from .compression import compress_for_qq
```

并把 `"compress_for_qq"` 加入 `__all__`。

- [ ] **Step 4: 运行压缩测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_compression.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/compression.py
```

Expected: `2 passed`，pyflakes 无输出。

- [ ] **Step 5: 提交压缩核心**

```powershell
git add hikari_bot/features/cardrush/reporting tests/cardrush/test_compression.py
git commit -m "feat: compress Cardrush QQ report images"
```

---

### Task 2: 接入手动和自动 QQ 发送路径

**Files:**

- Create: `hikari_bot/plugins/monitors/cardrush_delivery.py`
- Create: `tests/cardrush/test_qq_delivery.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py:24-43,269-285,356-385`
- Modify: `tests/cardrush/test_plugin_import.py`

**Interfaces:**

- Consumes: `compress_for_qq(bytes, max_bytes=...) -> bytes`
- Produces: `prepare_qq_pages(pages: Sequence[bytes], max_bytes: int = 1_000_000) -> Awaitable[list[bytes]]`

- [ ] **Step 1: 写入失败的 QQ 页面准备测试**

创建 `tests/cardrush/test_qq_delivery.py`：

```python
import asyncio

import hikari_bot.plugins.monitors.cardrush_delivery as delivery


def test_prepare_qq_pages_compresses_each_page_once_and_logs_sizes(
    monkeypatch,
):
    calls = []
    logs = []

    def fake_compress(page, max_bytes=1_000_000):
        calls.append((page, max_bytes))
        return b"compressed-" + page

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(
        delivery.prepare_qq_pages([b"one", b"two"])
    )

    assert result == [b"compressed-one", b"compressed-two"]
    assert calls == [
        (b"one", 1_000_000),
        (b"two", 1_000_000),
    ]
    assert "page 1/2" in logs[0]
    assert "3 -> 14 bytes" in logs[0]
```

在 `tests/cardrush/test_plugin_import.py` 增加：

```python
def test_qq_delivery_uses_compressed_pages_and_forward_targets():
    source = Path(
        "hikari_bot/plugins/monitors/cardrush.py"
    ).read_text(encoding="utf-8")

    assert source.count("await prepare_qq_pages(") == 2
    assert "for page in qq_pages:" in source
    assert "for screenshot in qq_screenshots:" in source
    assert "post_article_with_images(screenshots, date_str)" in source
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
```

Expected: collection fails because `cardrush_delivery.py` does not exist。

- [ ] **Step 3: 实现异步 QQ 页面准备器**

创建 `hikari_bot/plugins/monitors/cardrush_delivery.py`：

```python
import asyncio
from collections.abc import Sequence

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq


async def prepare_qq_pages(
    pages: Sequence[bytes],
    max_bytes: int = 1_000_000,
) -> list[bytes]:
    compressed_pages: list[bytes] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        compressed = await asyncio.to_thread(
            compress_for_qq,
            page,
            max_bytes=max_bytes,
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes"
        )
        compressed_pages.append(compressed)
    return compressed_pages
```

- [ ] **Step 4: 修改手动图报为发送压缩副本**

在 `hikari_bot/plugins/monitors/cardrush.py` 导入：

```python
from hikari_bot.plugins.monitors.cardrush_delivery import (
    prepare_qq_pages,
)
```

把手动图报发送段改为：

```python
pages = await report_workflow.render_for_date(date_str)
qq_pages = await prepare_qq_pages(pages)
await bot.send(
    event,
    f"下载完毕，正在发送 {len(qq_pages)} 页图报…",
)
for page in qq_pages:
    encoded = base64.b64encode(page).decode()
    await bot.send(
        event,
        MessageSegment.image(f"base64://{encoded}"),
    )
```

- [ ] **Step 5: 修改自动日报为复用压缩副本**

在 `_auto_send_daily_report` 中，无变化检查之后、管理员循环之前加入：

```python
qq_screenshots = await prepare_qq_pages(screenshots)
```

管理员发送循环改为：

```python
for user_id in ADMIN:
    for screenshot in qq_screenshots:
        encoded = base64.b64encode(screenshot).decode()
        await bot.send_private_msg(
            user_id=int(user_id),
            message=MessageSegment.image(
                f"base64://{encoded}"
            ),
        )
```

保留以下 B 站调用不变，确保使用原始 PNG：

```python
await post_article_with_images(screenshots, date_str)
```

- [ ] **Step 6: 运行集成测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py
```

Expected: 所有目标测试通过，pyflakes 无输出，`cardrush.py` 仍少于 450 行。

- [ ] **Step 7: 提交 QQ 发送集成**

```powershell
git add hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py tests/cardrush
git commit -m "fix: compress Cardrush images before QQ delivery"
```

---

### Task 3: 全量验证和真实图报压缩冒烟测试

**Files:**

- Modify only files required to correct failures introduced by Tasks 1-2.

**Interfaces:**

- Verifies: `compress_for_qq`、`prepare_qq_pages`、NoneBot 插件加载和原始 PNG/QQ JPEG 分流。

- [ ] **Step 1: 运行全量测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: 全部测试通过，无失败或收集错误。

- [ ] **Step 2: 运行编译和 Cardrush 静态检查**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q bot.py hikari_bot scripts
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py
```

Expected: 两条命令退出码均为 `0`，pyflakes 无输出。

- [ ] **Step 3: 运行真实 Playwright + QQ 压缩冒烟测试**

使用一页内存数据运行：

```powershell
@'
import asyncio
from io import BytesIO

from PIL import Image

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.renderer import (
    DailyReportRenderer,
    PlaywrightScreenshotBackend,
)
from hikari_bot.plugins.monitors.cardrush_delivery import (
    prepare_qq_pages,
)


class NoopImageFetcher:
    async def fetch(self, changes, image_dir):
        return {}


change = PriceChange(
    1,
    "smoke-card",
    "ウルトラ",
    "TEST-JP001",
    1000,
    1200,
    "changed",
    200,
    20.0,
    "2026-07-23T00:00:00.000Z",
)
renderer = DailyReportRenderer(
    NoopImageFetcher(),
    PlaywrightScreenshotBackend(),
)
originals = asyncio.run(
    renderer.render([change], "2026-07-23")
)
compressed = asyncio.run(prepare_qq_pages(originals))

assert originals[0].startswith(b"\x89PNG\r\n\x1a\n")
assert compressed[0].startswith(b"\xff\xd8")
assert len(compressed[0]) <= 1_000_000
with Image.open(BytesIO(compressed[0])) as image:
    assert image.format == "JPEG"
print(f"original={len(originals[0])}")
print(f"qq={len(compressed[0])}")
'@ | .\.venv\Scripts\python.exe -
```

Expected: 原始页为 PNG，QQ 页为 JPEG，QQ 页不超过 `1_000_000` 字节。

- [ ] **Step 4: 验证插件加载、差异和工作树**

Run:

```powershell
@'
import nonebot

nonebot.init()
plugin = nonebot.load_plugin(
    "hikari_bot.plugins.monitors.cardrush"
)
assert plugin is not None
print("cardrush_plugin_load=ok")
'@ | .\.venv\Scripts\python.exe -
git diff --check
git status --short
```

Expected: 插件加载成功，`git diff --check` 无输出；工作树只包含本计划要求的文件，或在提交后为空。

- [ ] **Step 5: 提交验证修正（仅在需要时）**

如果验证引入了代码修正：

```powershell
git add hikari_bot/features/cardrush/reporting hikari_bot/plugins/monitors tests/cardrush
git commit -m "test: verify Cardrush QQ image compression"
```

如果没有修正，不创建空提交。
