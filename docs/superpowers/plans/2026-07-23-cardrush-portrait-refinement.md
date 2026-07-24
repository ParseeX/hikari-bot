# Cardrush Portrait Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定底部水印、放大首页卡图，并把 QQ 图报改为常规约 200 KB 的 WebP。

**Architecture:** CSS 继续使用固定 1080×1920 画布，通过绝对定位水印释放流式布局空间，并分别设置详情页与首页卡高。QQ 压缩接口名称和调用边界不变，只把内部编码器从 JPEG 改为带质量下限的 WebP，其他消费者继续接收原始 PNG。

**Tech Stack:** Python 3.10+、Pillow、Playwright、HTML/CSS、asyncio、pytest、pyflakes。

## Global Constraints

- 水印距离画布底部保持 24 像素，不贴边。
- 详情页卡片高度为 238 像素。
- 首页卡片高度为 270 像素。
- 首页 5×5、详情页 5×7 和 1080×1920 画布保持不变。
- QQ 输出格式为 WebP，常规软目标为 200,000 字节。
- 质量依次尝试 80、75、70、65、60；质量 60 是清晰度下限。
- 超过 230,000 字节只记录警告，不缩小成品图。
- QQ-only 分流保持不变，其他消费者继续接收原始 PNG。
- 不改变分页、卡片字段、网站功能或 `retcode=1200` 行为。
- 所有生产代码修改先写失败测试并确认 RED。

## File Map

**Modify**

- `hikari_bot/features/cardrush/reporting/templates/daily_report.css`：水印定位及首页、详情页卡高。
- `hikari_bot/features/cardrush/reporting/compression.py`：WebP 软目标编码器。
- `hikari_bot/plugins/monitors/cardrush_delivery.py`：200 KB 默认目标、230 KB 警告和格式日志。
- `tests/cardrush/test_html_report.py`：固定布局契约。
- `tests/cardrush/test_compression.py`：WebP 格式、尺寸和质量序列。
- `tests/cardrush/test_qq_delivery.py`：目标、警告线和格式日志。

---

### Task 1: 固定水印并放大卡图

**Files:**

- Modify: `hikari_bot/features/cardrush/reporting/templates/daily_report.css`
- Modify: `tests/cardrush/test_html_report.py`

**Interfaces:**

- Consumes: 现有 `.content-wrap`、`.card`、`.grid-overview` 和 `.watermark`。
- Produces: 238 像素详情卡、270 像素首页卡和距画布底部 24 像素的水印。

- [ ] **Step 1: 写入失败的布局测试**

在 `tests/cardrush/test_html_report.py` 增加：

```python
from pathlib import Path


_CSS_PATH = Path(
    "hikari_bot/features/cardrush/reporting/templates/"
    "daily_report.css"
)


def css_block(selector: str) -> str:
    css = _CSS_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"{re.escape(selector)}\s*\{{([^}}]+)\}}",
        css,
        re.DOTALL,
    )
    assert match
    return match.group(1)


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
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py -q
```

Expected: FAIL；当前通用卡高为 236 像素，没有首页专属卡高，水印仍使用
`margin-top`。

- [ ] **Step 3: 修改固定布局**

在 `daily_report.css` 中把通用卡片高度改为：

```css
.card {
    height: 238px;
    border-radius: 8px;
    overflow: hidden;
    position: relative;
    background: #0a1020;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.55);
}
```

在网格规则后增加：

```css
.grid-overview .card {
    height: 270px;
}
```

把水印规则改为：

```css
.watermark {
    position: absolute;
    right: 2px;
    bottom: 0;
    height: 24px;
    text-align: right;
    font-size: 13px;
    font-weight: 700;
    line-height: 24px;
    letter-spacing: 1px;
    color: rgba(255, 255, 255, 0.72);
}
```

- [ ] **Step 4: 运行布局测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py -q
```

Expected: 目标测试全部通过。

- [ ] **Step 5: 提交布局细化**

```powershell
git add hikari_bot/features/cardrush/reporting/templates/daily_report.css tests/cardrush/test_html_report.py
git commit -m "feat: refine Cardrush portrait spacing"
```

---

### Task 2: 把 QQ 压缩改为 WebP 软目标

**Files:**

- Modify: `hikari_bot/features/cardrush/reporting/compression.py`
- Modify: `hikari_bot/plugins/monitors/cardrush_delivery.py`
- Modify: `tests/cardrush/test_compression.py`
- Modify: `tests/cardrush/test_qq_delivery.py`

**Interfaces:**

- Produces: `compress_for_qq(image_bytes: bytes, target_bytes: int = 200_000) -> bytes`。
- Produces: `prepare_qq_pages(pages: Sequence[bytes], target_bytes: int = 200_000) -> Awaitable[list[bytes]]`。
- Produces: `_image_info(image_bytes: bytes) -> tuple[int, int, str]`。

- [ ] **Step 1: 写入失败的 WebP 压缩测试**

把 `tests/cardrush/test_compression.py` 改为：

```python
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

import hikari_bot.features.cardrush.reporting.compression as compression
from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.reporting.compression import (
    compress_for_qq,
)


def report_like_png() -> bytes:
    image = Image.new("RGB", (1080, 1920), "#081020")
    draw = ImageDraw.Draw(image)
    for row in range(7):
        for column in range(5):
            left = 24 + column * 206
            top = 140 + row * 244
            draw.rectangle(
                (left, top, left + 196, top + 232),
                fill=(20 + row * 8, 30 + column * 8, 55),
            )
            draw.rectangle(
                (left, top + 135, left + 196, top + 232),
                fill="#0a0f1d",
            )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compress_for_qq_returns_webp_without_resizing():
    result = compress_for_qq(
        report_like_png(),
        target_bytes=200_000,
    )

    assert result.startswith(b"RIFF")
    assert result[8:12] == b"WEBP"
    assert len(result) <= 200_000
    with Image.open(BytesIO(result)) as image:
        assert image.format == "WEBP"
        assert image.size == (1080, 1920)


def test_compress_for_qq_returns_quality_floor_when_target_is_missed(
    monkeypatch,
):
    calls = []

    def fake_encode(image, quality):
        calls.append((image.size, quality))
        return bytes(500)

    monkeypatch.setattr(compression, "_encode_webp", fake_encode)

    result = compress_for_qq(
        report_like_png(),
        target_bytes=100,
    )

    assert len(result) == 500
    assert calls == [
        ((1080, 1920), 80),
        ((1080, 1920), 75),
        ((1080, 1920), 70),
        ((1080, 1920), 65),
        ((1080, 1920), 60),
    ]


def test_compress_for_qq_rejects_invalid_image():
    with pytest.raises(CardrushRenderError, match="decode"):
        compress_for_qq(b"not-an-image")
```

- [ ] **Step 2: 运行压缩测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_compression.py -q
```

Expected: FAIL；当前输出为 JPEG，默认目标为 350,000 字节，且不存在
`_encode_webp`。

- [ ] **Step 3: 实现 WebP 编码器**

把 `compression.py` 改为：

```python
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 80
_MIN_QUALITY = 60
_QUALITY_STEP = 5
_TARGET_BYTES = 200_000


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="WEBP",
        quality=quality,
        method=6,
    )
    return buffer.getvalue()


def compress_for_qq(
    image_bytes: bytes,
    target_bytes: int = _TARGET_BYTES,
) -> bytes:
    if target_bytes <= 0:
        raise ValueError("target_bytes must be positive")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            working = source.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise CardrushRenderError(
            f"Unable to decode QQ report image: {error}"
        ) from error

    encoded = b""
    for quality in range(
        _MAX_QUALITY,
        _MIN_QUALITY - 1,
        -_QUALITY_STEP,
    ):
        encoded = _encode_webp(working, quality)
        if len(encoded) <= target_bytes:
            return encoded
    return encoded
```

- [ ] **Step 4: 写入失败的 QQ 目标和格式日志测试**

把 `tests/cardrush/test_qq_delivery.py` 改为：

```python
import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path

from PIL import Image

module_path = Path(
    "hikari_bot/plugins/monitors/cardrush_delivery.py"
)
spec = importlib.util.spec_from_file_location(
    "cardrush_test_delivery",
    module_path,
)
assert spec and spec.loader
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def webp_bytes(size=(1080, 1920)) -> bytes:
    image = Image.new("RGB", size, "#081020")
    buffer = BytesIO()
    image.save(buffer, format="WEBP")
    return buffer.getvalue()


def test_prepare_qq_pages_uses_webp_target_and_logs_format(
    monkeypatch,
):
    calls = []
    logs = []
    compressed = webp_bytes()

    def fake_compress(page, target_bytes=200_000):
        calls.append((page, target_bytes))
        return compressed

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert result == [compressed]
    assert calls == [(b"one", 200_000)]
    assert "1080x1920" in logs[0]
    assert "WEBP" in logs[0]
    assert "WARNING" not in logs[0]


def test_prepare_qq_pages_warns_above_observation_limit(
    monkeypatch,
):
    logs = []

    def fake_compress(page, target_bytes=200_000):
        return bytes(230_001)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)
    monkeypatch.setattr(
        delivery,
        "_image_info",
        lambda data: (1080, 1920, "WEBP"),
    )

    asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert "WARNING: above 230000 bytes" in logs[0]
```

- [ ] **Step 5: 运行 QQ 测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_qq_delivery.py -q
```

Expected: FAIL；当前默认目标和警告线仍为 350,000/450,000，日志没有格式。

- [ ] **Step 6: 更新 QQ 页面准备器**

把 `cardrush_delivery.py` 改为：

```python
import asyncio
from collections.abc import Sequence
from io import BytesIO

from PIL import Image

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq

_TARGET_BYTES = 200_000
_WARNING_BYTES = 230_000


def _image_info(
    image_bytes: bytes,
) -> tuple[int, int, str]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.width, image.height, image.format or "UNKNOWN"


async def prepare_qq_pages(
    pages: Sequence[bytes],
    target_bytes: int = _TARGET_BYTES,
) -> list[bytes]:
    compressed_pages: list[bytes] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        compressed = await asyncio.to_thread(
            compress_for_qq,
            page,
            target_bytes=target_bytes,
        )
        width, height, image_format = _image_info(compressed)
        warning = (
            f", WARNING: above {_WARNING_BYTES} bytes"
            if len(compressed) > _WARNING_BYTES
            else ""
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes, "
            f"{width}x{height} {image_format}{warning}"
        )
        compressed_pages.append(compressed)
    return compressed_pages
```

- [ ] **Step 7: 运行压缩、QQ 和分流测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_compression.py tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/compression.py hikari_bot/plugins/monitors/cardrush_delivery.py
```

Expected: 所有目标测试通过，pyflakes 无输出。

- [ ] **Step 8: 提交 WebP 压缩**

```powershell
git add hikari_bot/features/cardrush/reporting/compression.py hikari_bot/plugins/monitors/cardrush_delivery.py tests/cardrush/test_compression.py tests/cardrush/test_qq_delivery.py
git commit -m "feat: encode Cardrush QQ reports as WebP"
```

---

### Task 3: 全量验证和预览验收

**Files:**

- Modify only files required to correct failures introduced by Tasks 1-2.

**Interfaces:**

- Verifies: 固定水印、238/270 像素卡高、1080×1920、WebP、约 200～230 KB 和 QQ-only 分流。

- [ ] **Step 1: 运行全量测试和静态检查**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q bot.py hikari_bot scripts
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py
```

Expected: pytest 全部通过，compileall 和 pyflakes 退出码为 0。

- [ ] **Step 2: 生成当前首页和详情页 WebP 预览**

Run:

```powershell
@'
import asyncio
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.compression import (
    compress_for_qq,
)
from hikari_bot.features.cardrush.reporting.renderer import (
    DailyReportRenderer,
    PlaywrightScreenshotBackend,
)
from hikari_bot.features.cardrush.reporting.thumbnails import (
    write_card_thumbnail,
)


class PreviewImageFetcher:
    async def fetch(self, changes, image_dir):
        result = {}
        for change in changes:
            source = Image.new(
                "RGB",
                (500, 700),
                (
                    28 + (change.product_id * 37) % 120,
                    38 + (change.product_id * 53) % 110,
                    55 + (change.product_id * 71) % 130,
                ),
            )
            draw = ImageDraw.Draw(source)
            draw.rectangle(
                (35, 35, 465, 665),
                outline=(224, 195, 95),
                width=12,
            )
            draw.rectangle(
                (60, 70, 440, 390),
                outline=(245, 225, 150),
                width=5,
            )
            buffer = BytesIO()
            source.save(buffer, format="PNG")
            destination = image_dir / f"{change.product_id}.jpg"
            write_card_thumbnail(
                buffer.getvalue(),
                destination,
            )
            result[change.product_id] = (
                destination.resolve().as_uri()
            )
        return result


changes = [
    PriceChange(
        product_id=index,
        name=f"preview-card-{index}",
        rarity="\u30a6\u30eb\u30c8\u30e9",
        model_number=f"TEST-JP{index:03d}",
        old_price=1000 + index * 20,
        new_price=1200 + index * 35,
        change_type="changed",
        price_diff=200 + index * 15,
        percent_diff=5.0 + index,
        changed_at="2026-07-23T00:00:00.000Z",
    )
    for index in range(1, 61)
]
renderer = DailyReportRenderer(
    PreviewImageFetcher(),
    PlaywrightScreenshotBackend(),
)
pages = asyncio.run(
    renderer.render(changes, "2026-07-23")
)
assert len(pages) == 2

output_dir = (
    Path(tempfile.gettempdir())
    / "cardrush-current-preview-webp"
)
output_dir.mkdir(parents=True, exist_ok=True)
for label, page in zip(
    ("homepage", "detail-page"),
    pages,
):
    compressed = compress_for_qq(page)
    with Image.open(BytesIO(compressed)) as image:
        assert image.size == (1080, 1920)
        assert image.format == "WEBP"
    assert len(compressed) <= 230_000
    output_path = output_dir / f"{label}.webp"
    output_path.write_bytes(compressed)
    print(
        f"{output_path} bytes={len(compressed)}"
    )
'@ | .\.venv\Scripts\python.exe -
```

Expected: 输出 `homepage.webp` 和 `detail-page.webp`，两页均不超过
230,000 字节。

- [ ] **Step 3: 视觉检查**

使用图片查看工具打开两张 WebP，确认：

- 水印距底部约 24 像素；
- 首页卡片明显高于旧版，底部无大块空白；
- 详情页 35 张卡完整显示；
- 卡名、型号、新旧价格和 badge 无裁切；
- 字体没有明显压缩模糊。

- [ ] **Step 4: 验证插件和工作树**

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

Expected: 插件加载成功；工作树没有未提交修改。

- [ ] **Step 5: 推送 main**

```powershell
git push origin main
```

Expected: 远端 `main` 更新到本地 HEAD。
