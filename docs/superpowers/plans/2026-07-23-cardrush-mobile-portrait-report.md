# Cardrush Mobile Portrait Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Cardrush 图片日报改为手机可完整查看的 1080×1920 竖版，并在不缩小成品图的前提下把 QQ JPEG 通常控制在 250～350 KB。

**Architecture:** 保留现有数据服务和 QQ 发送边界，在 HTML 层完成 TOP 25 与每页 35 张分页，在渲染层固定 9:16 画布并预制卡图缩略图，在 QQ 适配层使用带清晰度下限的软目标 JPEG 压缩。每层保持现有接口方向：工作流返回原始 PNG，只有 QQ 发送副本经过压缩。

**Tech Stack:** Python 3.10+、Pillow、Playwright、HTML/CSS、asyncio、NoneBot 2、pytest、pyflakes。

## Global Constraints

- 固定画布为 `1080×1920`，成品图不得二次缩放。
- 首页展示精简统计面板和异动 TOP 25。
- 详情页为 5 列×7 行，每页最多 35 张。
- 保留竖向卡图和下半部卡名、型号、价格信息层。
- QQ 常规目标为 `250～350 KB`；超过 `450 KB` 时记录警告而不是破坏字体。
- JPEG 质量从约 82 搜索到约 72，不再降到 40。
- 卡图失败时使用默认卡背，不中断整份图报。
- QQ 压缩只影响手动图报和自动日报；其他消费者仍接收原始截图。
- 不实现网站页面或网站发布流程。
- 不增加 `retcode=1200` 自动重试。
- 每个生产代码步骤必须先有一个因行为尚未满足而失败的测试。

## File Map

**Create**

- `hikari_bot/features/cardrush/reporting/thumbnails.py`：把下载的原始卡图裁剪、缩放和编码为展示尺寸缩略图。
- `tests/cardrush/test_thumbnails.py`：缩略图尺寸、格式和错误测试。

**Modify**

- `hikari_bot/features/cardrush/reporting/html.py`：TOP 25、每页 35 张、紧凑首页统计文案及可测试的卡片标识。
- `hikari_bot/features/cardrush/reporting/templates/daily_report.css`：1080×1920、5 列竖向卡片和清晰文字样式。
- `hikari_bot/features/cardrush/reporting/renderer.py`：1080×1920 Playwright 视口、溢出检查和缩略图接入。
- `hikari_bot/features/cardrush/reporting/compression.py`：软目标、质量下限和禁止整图缩放。
- `hikari_bot/plugins/monitors/cardrush_delivery.py`：350 KB 默认目标、尺寸日志和 450 KB 警告。
- `tests/cardrush/test_html_report.py`：分页、首页摘要、布局常量和数据完整性。
- `tests/cardrush/test_report_workflow.py`：渲染视口契约。
- `tests/cardrush/test_compression.py`：软目标、清晰度下限和尺寸不变。
- `tests/cardrush/test_qq_delivery.py`：默认目标、尺寸日志和超限警告。

---

### Task 1: 改造首页和详情分页

**Files:**

- Modify: `hikari_bot/features/cardrush/reporting/html.py:10-205`
- Modify: `tests/cardrush/test_html_report.py`

**Interfaces:**

- Consumes: `Sequence[PriceChange]`、日期和卡图 URL 映射。
- Produces: `render_daily_report_html(...) -> list[str]`，第 1 页最多 25 张，后续页每页最多 35 张。
- Produces: 每张真实卡片的 `data-product-id="<id>"`，仅用于稳定测试和页面诊断。

- [ ] **Step 1: 写入失败的分页和首页摘要测试**

把 `tests/cardrush/test_html_report.py` 扩展为：

```python
import re

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.html import (
    render_daily_report_html,
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
    assert "overview-desc-zh" not in page
    assert "overview-desc-ja" not in page
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py -q
```

Expected: FAIL；现有分页仍是 TOP 30 和每页 50 张，也没有 `data-product-id` 与新版摘要文案。

- [ ] **Step 3: 修改分页常量和卡片标识**

在 `html.py` 中改为：

```python
_PAGE_SIZE = 35
_OVERVIEW_SIZE = 25
```

把 `_card_html` 返回值的根节点改为：

```python
return f"""
    <div class="card {css_class}"
         data-product-id="{change.product_id}">
      <img class="card-img" src="{image_url}" loading="lazy">
      <div class="card-overlay">
        <div class="card-name">{name}</div>
        <div class="card-meta">{model_number} {rarity}</div>
        <div class="price-row">
          <div class="price-block">
            <div class="new-price">{change.new_price:,}円</div>
            {old_html}
          </div>
          <span class="badge">{badge}</span>
        </div>
      </div>
    </div>"""
```

保留现有 `new`、`up`、`down` 分支对 `old_html` 和 `badge` 的计算。

- [ ] **Step 4: 用紧凑统计面板替换双语长段落**

删除 `date_display`、`date_display_ja`、`chinese_summary` 和
`japanese_summary`，把 `overview_body` 改为：

```python
overview_body = f"""
  <div class="overview-extra">
    <div class="overview-stats">
      <div class="stat-line stat-range">
        <span class="stat-label">统计范围</span>
        <strong>500～100,000円</strong>
      </div>
      <div class="stat-line stat-total">
        <span class="stat-label">今日异动</span>
        <strong>{len(changes)}</strong><span>张</span>
      </div>
      <div class="stat-counts">
        <span class="num-up">↑ 涨价 {up_count}</span>
        <span class="num-down">↓ 降价 {down_count}</span>
        <span class="num-new">新增 {new_count}</span>
      </div>
      <div class="stat-ja">
        買取価格の変動：{len(changes)}枚
      </div>
    </div>
    <div class="overview-section-title">
      <span>异动 TOP {_OVERVIEW_SIZE}</span>
    </div>
  </div>
  <div class="grid grid-overview">{overview_cards}
  </div>"""
```

- [ ] **Step 5: 运行测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/html.py
```

Expected: `2 passed`，pyflakes 无输出。

- [ ] **Step 6: 提交分页和首页文案**

```powershell
git add hikari_bot/features/cardrush/reporting/html.py tests/cardrush/test_html_report.py
git commit -m "feat: paginate Cardrush portrait reports"
```

---

### Task 2: 实现固定 1080×1920 竖版布局

**Files:**

- Modify: `hikari_bot/features/cardrush/reporting/templates/daily_report.css`
- Modify: `hikari_bot/features/cardrush/reporting/renderer.py:116-155`
- Modify: `tests/cardrush/test_html_report.py`
- Modify: `tests/cardrush/test_report_workflow.py`

**Interfaces:**

- Consumes: Task 1 生成的 `.overview-stats`、`.grid`、`.grid-overview` 和卡片结构。
- Produces: `PlaywrightScreenshotBackend.capture(...) -> list[bytes]`，每张 PNG 固定为 1080×1920。
- Produces: `_VIEWPORT = {"width": 1080, "height": 1920}`。

- [ ] **Step 1: 写入失败的竖版布局契约测试**

在 `tests/cardrush/test_html_report.py` 增加：

```python
def test_html_report_embeds_mobile_portrait_layout():
    page = render_daily_report_html(
        make_changes(35),
        "2026-07-23",
        image_map={},
    )[0]

    assert "width: 1080px" in page
    assert "height: 1920px" in page
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in page
    assert "font-size: 18px" in page
    assert "font-size: 28px" in page
```

在 `tests/cardrush/test_report_workflow.py` 的导入中加入 `_VIEWPORT`，并增加：

```python
def test_screenshot_backend_uses_mobile_portrait_viewport():
    assert _VIEWPORT == {"width": 1080, "height": 1920}
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py tests/cardrush/test_report_workflow.py -q
```

Expected: FAIL；现有 CSS 仍要求最小宽度 1300，Playwright 仍使用 1340×900。

- [ ] **Step 3: 把 CSS 改为固定 9:16 和 5 列卡片**

用以下尺寸和选择器替换现有桌面布局；未列出的涨跌颜色类继续保留现有配色：

```css
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
    width: 1080px;
    height: 1920px;
    overflow: hidden;
}
body {
    font-family: "Noto Sans CJK JP", "Source Han Sans JP", "Yu Gothic",
                 "Meiryo", "Microsoft YaHei", sans-serif;
    __PAGE_BACKGROUND__
    color: #f2f6fb;
    padding: 24px;
    position: relative;
}
body::before {
    content: "";
    position: absolute;
    inset: 0;
    background: rgba(4, 7, 16, 0.76);
    z-index: 0;
    pointer-events: none;
}
.content-wrap {
    position: relative;
    z-index: 1;
    width: 1032px;
    height: 1872px;
}
.header {
    height: 110px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    padding: 14px 20px;
    background: rgba(11, 18, 34, 0.94);
    border: 1px solid rgba(160, 200, 240, 0.22);
    border-radius: 12px;
}
.header-left {
    padding-left: 14px;
    border-left: 5px solid #60b0ff;
}
.header-title {
    font-size: 34px;
    font-weight: 900;
    letter-spacing: 2px;
    line-height: 1.15;
    color: #ffffff;
}
.header-eyebrow {
    margin-top: 4px;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 4px;
    color: #a8cef0;
}
.header-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 5px;
}
.header-date-main {
    font-size: 27px;
    font-weight: 900;
    letter-spacing: 2px;
    color: #ffffff;
}
.header-page-num {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #8db7d2;
}
.grid, .grid-overview {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 8px;
}
.card {
    height: 236px;
    border-radius: 8px;
    overflow: hidden;
    position: relative;
    background: #0a1020;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.55);
}
.card-img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center top;
    display: block;
    background: #0c1428;
}
.card-overlay {
    position: absolute;
    inset: auto 0 0;
    min-height: 128px;
    padding: 34px 8px 8px;
    background: linear-gradient(
        to bottom,
        rgba(5, 8, 18, 0) 0%,
        rgba(5, 8, 18, 0.90) 28%,
        rgba(5, 8, 18, 0.98) 55%
    );
}
.card-name {
    min-height: 45px;
    font-size: 18px;
    font-weight: 800;
    line-height: 1.25;
    color: #ffffff;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    word-break: break-all;
}
.card-meta {
    margin: 2px 0 4px;
    font-size: 14px;
    font-weight: 700;
    line-height: 1.2;
    color: #dce9f5;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.price-row {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 4px;
}
.price-block { flex: 1; min-width: 0; }
.new-price {
    font-size: 28px;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
}
.old-price {
    margin-top: 2px;
    font-size: 15px;
    font-weight: 700;
    line-height: 1.1;
    color: #e0b870;
    text-decoration: line-through;
    white-space: nowrap;
}
.badge {
    flex-shrink: 0;
    align-self: flex-end;
    padding: 2px 5px;
    border-radius: 4px;
    font-size: 14px;
    font-weight: 800;
    line-height: 1.3;
}
.up .card-overlay,
.new .card-overlay {
    background: linear-gradient(
        to bottom,
        rgba(44, 22, 5, 0) 0%,
        rgba(62, 31, 6, 0.92) 28%,
        rgba(48, 24, 5, 0.99) 55%
    );
}
.down .card-overlay {
    background: linear-gradient(
        to bottom,
        rgba(4, 35, 18, 0) 0%,
        rgba(5, 48, 24, 0.92) 28%,
        rgba(4, 37, 19, 0.99) 55%
    );
}
.up .new-price,
.new .new-price { color: #ffe066; }
.down .new-price { color: #b5ffd1; }
.up .badge,
.new .badge {
    color: #ffe066;
    background: #59480c;
    border: 1px solid #9c8121;
}
.down .badge {
    color: #b5ffd1;
    background: #154d2a;
    border: 1px solid #2d8250;
}
.card-placeholder {
    border: none;
    background: transparent;
    box-shadow: none;
}
.watermark {
    height: 24px;
    margin-top: 8px;
    padding-right: 2px;
    text-align: right;
    font-size: 13px;
    font-weight: 700;
    line-height: 24px;
    letter-spacing: 1px;
    color: rgba(255, 255, 255, 0.72);
}
.overview-extra {
    height: 330px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 18px;
}
.overview-stats {
    min-height: 220px;
    padding: 24px 34px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    align-content: center;
    gap: 14px 32px;
    background: rgba(8, 14, 28, 0.95);
    border: 1px solid rgba(120, 175, 225, 0.20);
    border-radius: 10px;
}
.stat-line {
    display: flex;
    align-items: baseline;
    gap: 12px;
    font-size: 24px;
}
.stat-line strong {
    font-size: 32px;
    font-weight: 900;
    color: #ffffff;
}
.stat-label {
    font-weight: 700;
    color: #a8cbe7;
}
.stat-counts {
    grid-column: 1 / -1;
    display: flex;
    gap: 36px;
    font-size: 25px;
    font-weight: 900;
}
.stat-ja {
    grid-column: 1 / -1;
    padding-top: 10px;
    border-top: 1px solid rgba(120, 175, 225, 0.18);
    font-size: 18px;
    font-weight: 700;
    color: #b8cadd;
}
.overview-section-title {
    display: flex;
    align-items: center;
    gap: 12px;
}
.overview-section-title::before,
.overview-section-title::after {
    content: "";
    flex: 1;
    height: 1px;
    background: rgba(120, 175, 225, 0.35);
}
.overview-section-title span {
    font-size: 28px;
    font-weight: 900;
    letter-spacing: 3px;
    color: #a9c9e0;
}
```

完整样式表由上述基础、卡片状态配色和首页样式组成；不得重新加入文字滤镜、
`backdrop-filter` 或大范围发光阴影。

- [ ] **Step 4: 固定 Playwright 视口并拒绝溢出页面**

在 `renderer.py` 的协议定义之后增加：

```python
_VIEWPORT = {"width": 1080, "height": 1920}
```

把 `browser.new_page` 和截图段改为：

```python
page = await browser.new_page(viewport=_VIEWPORT)
try:
    page.set_default_timeout(120_000)
    await page.goto(
        html_path.resolve().as_uri(),
        wait_until="domcontentloaded",
    )
    await page.evaluate("document.fonts.ready")
    dimensions = await page.evaluate(
        """() => ({
            width: document.documentElement.scrollWidth,
            height: document.documentElement.scrollHeight,
        })"""
    )
    if (
        dimensions["width"] > _VIEWPORT["width"]
        or dimensions["height"] > _VIEWPORT["height"]
    ):
        raise CardrushRenderError(
            "Cardrush report exceeds 1080x1920 viewport: "
            f"{dimensions['width']}x{dimensions['height']}"
        )
    screenshots.append(
        await page.screenshot(
            full_page=False,
            animations="disabled",
            timeout=120_000,
        )
    )
finally:
    await page.close()
```

- [ ] **Step 5: 运行布局测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_html_report.py tests/cardrush/test_report_workflow.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/renderer.py
```

Expected: 所有目标测试通过，pyflakes 无输出。

- [ ] **Step 6: 提交竖版渲染**

```powershell
git add hikari_bot/features/cardrush/reporting/templates/daily_report.css hikari_bot/features/cardrush/reporting/renderer.py tests/cardrush/test_html_report.py tests/cardrush/test_report_workflow.py
git commit -m "feat: render Cardrush mobile portrait pages"
```

---

### Task 3: 预制卡图缩略图

**Files:**

- Create: `hikari_bot/features/cardrush/reporting/thumbnails.py`
- Create: `tests/cardrush/test_thumbnails.py`
- Modify: `hikari_bot/features/cardrush/reporting/renderer.py:37-114`

**Interfaces:**

- Produces: `write_card_thumbnail(image_bytes: bytes, destination: Path) -> None`。
- Output: 220×264、RGB JPEG、面向卡片顶部裁剪。
- Consumes: `AiohttpCardImageFetcher` 下载的原始图片字节和默认卡背字节。

- [ ] **Step 1: 写入失败的缩略图测试**

创建 `tests/cardrush/test_thumbnails.py`：

```python
from io import BytesIO

import pytest
from PIL import Image, UnidentifiedImageError

from hikari_bot.features.cardrush.reporting.thumbnails import (
    write_card_thumbnail,
)


def source_png() -> bytes:
    image = Image.new("RGB", (800, 1200), "#204080")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_write_card_thumbnail_creates_display_sized_jpeg(tmp_path):
    destination = tmp_path / "card.jpg"

    write_card_thumbnail(source_png(), destination)

    with Image.open(destination) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (220, 264)


def test_write_card_thumbnail_rejects_invalid_data(tmp_path):
    with pytest.raises(UnidentifiedImageError):
        write_card_thumbnail(
            b"not-an-image",
            tmp_path / "card.jpg",
        )
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_thumbnails.py -q
```

Expected: collection fails because `reporting.thumbnails` does not exist。

- [ ] **Step 3: 实现纯 Pillow 缩略图写入器**

创建 `hikari_bot/features/cardrush/reporting/thumbnails.py`：

```python
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

_THUMBNAIL_SIZE = (220, 264)


def write_card_thumbnail(
    image_bytes: bytes,
    destination: Path,
) -> None:
    with Image.open(BytesIO(image_bytes)) as source:
        source.load()
        thumbnail = ImageOps.fit(
            source.convert("RGB"),
            _THUMBNAIL_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.25),
        )
    thumbnail.save(
        destination,
        format="JPEG",
        quality=64,
        optimize=True,
        progressive=True,
        subsampling=2,
    )
```

- [ ] **Step 4: 在卡图下载器中写入和复用缩略图**

在 `renderer.py` 导入：

```python
from .thumbnails import write_card_thumbnail
```

把默认图片准备段改为：

```python
unknown_path = image_dir / "unknown.jpg"
if not unknown_path.exists():
    unknown_data = await get_unknown_card()
    if unknown_data:
        try:
            write_card_thumbnail(unknown_data, unknown_path)
        except (OSError, UnidentifiedImageError):
            pass
unknown_url = (
    unknown_path.resolve().as_uri()
    if unknown_path.exists()
    else ""
)
```

同时从 Pillow 导入 `UnidentifiedImageError`。把单卡目标文件和成功响应段改为：

```python
destination = image_dir / f"{product_id}.jpg"
```

```python
if response.status == 200:
    write_card_thumbnail(
        await response.read(),
        destination,
    )
    result[product_id] = (
        destination.resolve().as_uri()
    )
    return
```

现有重试循环继续捕获下载、解码和写入异常；全部尝试失败后使用
`unknown_url`。

- [ ] **Step 5: 运行缩略图和渲染回归测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_thumbnails.py tests/cardrush/test_report_workflow.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/thumbnails.py hikari_bot/features/cardrush/reporting/renderer.py
```

Expected: 所有目标测试通过，pyflakes 无输出。

- [ ] **Step 6: 提交缩略图准备**

```powershell
git add hikari_bot/features/cardrush/reporting/thumbnails.py hikari_bot/features/cardrush/reporting/renderer.py tests/cardrush/test_thumbnails.py
git commit -m "perf: prepare Cardrush report thumbnails"
```

---

### Task 4: 改为清晰度优先的 QQ 软目标压缩

**Files:**

- Modify: `hikari_bot/features/cardrush/reporting/compression.py`
- Modify: `hikari_bot/plugins/monitors/cardrush_delivery.py`
- Modify: `tests/cardrush/test_compression.py`
- Modify: `tests/cardrush/test_qq_delivery.py`

**Interfaces:**

- Produces: `compress_for_qq(image_bytes: bytes, target_bytes: int = 350_000) -> bytes`。
- Produces: `prepare_qq_pages(pages: Sequence[bytes], target_bytes: int = 350_000) -> Awaitable[list[bytes]]`。
- Runtime rule: 质量 82、80、78、76、74、72；第一个达到软目标的结果立即返回，否则返回质量 72。

- [ ] **Step 1: 用软目标和尺寸不变测试替换旧硬上限测试**

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


def test_compress_for_qq_hits_target_without_resizing():
    result = compress_for_qq(
        report_like_png(),
        target_bytes=350_000,
    )

    assert result.startswith(b"\xff\xd8")
    assert len(result) <= 350_000
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1920)


def test_compress_for_qq_returns_quality_floor_without_resizing(
    monkeypatch,
):
    calls = []

    def fake_encode(image, quality):
        calls.append((image.size, quality))
        return bytes(500)

    monkeypatch.setattr(compression, "_encode_jpeg", fake_encode)

    result = compress_for_qq(
        report_like_png(),
        target_bytes=100,
    )

    assert len(result) == 500
    assert calls == [
        ((1080, 1920), 82),
        ((1080, 1920), 80),
        ((1080, 1920), 78),
        ((1080, 1920), 76),
        ((1080, 1920), 74),
        ((1080, 1920), 72),
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

Expected: FAIL；现有函数参数仍叫 `max_bytes`，并会降质到 40 后缩小画布。

- [ ] **Step 3: 实现软目标 JPEG 编码**

把 `compression.py` 改为：

```python
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 82
_MIN_QUALITY = 72
_QUALITY_STEP = 2
_TARGET_BYTES = 350_000


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
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
        encoded = _encode_jpeg(working, quality)
        if len(encoded) <= target_bytes:
            return encoded
    return encoded
```

- [ ] **Step 4: 写入失败的 QQ 默认目标、尺寸和警告测试**

把 `tests/cardrush/test_qq_delivery.py` 的测试替换为：

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


def jpeg_bytes(size=(1080, 1920)) -> bytes:
    image = Image.new("RGB", size, "#081020")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_prepare_qq_pages_uses_target_and_logs_dimensions(
    monkeypatch,
):
    calls = []
    logs = []
    compressed = jpeg_bytes()

    def fake_compress(page, target_bytes=350_000):
        calls.append((page, target_bytes))
        return compressed

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)

    result = asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert result == [compressed]
    assert calls == [(b"one", 350_000)]
    assert "page 1/1" in logs[0]
    assert "1080x1920" in logs[0]
    assert "WARNING" not in logs[0]


def test_prepare_qq_pages_warns_above_observation_limit(
    monkeypatch,
):
    logs = []

    def fake_compress(page, target_bytes=350_000):
        return jpeg_bytes() + bytes(451_000)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "compress_for_qq", fake_compress)
    monkeypatch.setattr(delivery, "log_message", fake_log)
    monkeypatch.setattr(
        delivery,
        "_image_size",
        lambda data: (1080, 1920),
    )

    asyncio.run(delivery.prepare_qq_pages([b"one"]))

    assert "WARNING: above 450000 bytes" in logs[0]
```

- [ ] **Step 5: 运行 QQ 测试并确认 RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_qq_delivery.py -q
```

Expected: FAIL；现有准备器默认 1,000,000 字节且不记录尺寸和警告。

- [ ] **Step 6: 更新 QQ 页面准备器**

把 `cardrush_delivery.py` 改为：

```python
import asyncio
from collections.abc import Sequence
from io import BytesIO

from PIL import Image

from hikari_bot.core.logger import log_message
from hikari_bot.features.cardrush.reporting import compress_for_qq

_TARGET_BYTES = 350_000
_WARNING_BYTES = 450_000


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.size


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
        width, height = _image_size(compressed)
        warning = (
            f", WARNING: above {_WARNING_BYTES} bytes"
            if len(compressed) > _WARNING_BYTES
            else ""
        )
        await log_message(
            f"[cardrush] QQ image page {index}/{total}: "
            f"{len(page)} -> {len(compressed)} bytes, "
            f"{width}x{height}{warning}"
        )
        compressed_pages.append(compressed)
    return compressed_pages
```

- [ ] **Step 7: 运行压缩与 QQ 测试并确认 GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_compression.py tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush/reporting/compression.py hikari_bot/plugins/monitors/cardrush_delivery.py
```

Expected: 所有目标测试通过，pyflakes 无输出；插件测试继续证明只有 QQ 副本经过压缩。

- [ ] **Step 8: 提交清晰度优先压缩**

```powershell
git add hikari_bot/features/cardrush/reporting/compression.py hikari_bot/plugins/monitors/cardrush_delivery.py tests/cardrush/test_compression.py tests/cardrush/test_qq_delivery.py
git commit -m "fix: preserve Cardrush QQ report clarity"
```

---

### Task 5: 全量验证和真实竖版截图验收

**Files:**

- Modify only files required to correct failures introduced by Tasks 1-4.

**Interfaces:**

- Verifies: 1080×1920 PNG、TOP 25、35 张详情页、缩略图、QQ JPEG、尺寸不变、插件加载和 QQ-only 分流。

- [ ] **Step 1: 运行 Cardrush 全量测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush -q
```

Expected: Cardrush 测试全部通过，无失败或收集错误。

- [ ] **Step 2: 运行项目全量测试和静态检查**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q bot.py hikari_bot scripts
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/features/cardrush hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py hikari_bot/plugins/web/routes/cr_upload.py
```

Expected: pytest 全部通过；compileall 与 pyflakes 退出码为 0，pyflakes 无输出。

- [ ] **Step 3: 生成两页带合成卡图的真实 Playwright 样本**

Run:

```powershell
@'
import asyncio
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.renderer import (
    DailyReportRenderer,
    PlaywrightScreenshotBackend,
)
from hikari_bot.features.cardrush.reporting.thumbnails import (
    write_card_thumbnail,
)
from hikari_bot.plugins.monitors.cardrush_delivery import (
    prepare_qq_pages,
)


class SyntheticImageFetcher:
    async def fetch(self, changes, image_dir):
        result = {}
        for change in changes:
            source = Image.new(
                "RGB",
                (500, 700),
                (
                    20 + change.product_id * 3 % 180,
                    25 + change.product_id * 5 % 170,
                    35 + change.product_id * 7 % 160,
                ),
            )
            draw = ImageDraw.Draw(source)
            for offset in range(0, 700, 28):
                draw.line(
                    (0, offset, 500, 700 - offset),
                    fill=(220, 190, 80),
                    width=3,
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
        name=f"青眼の白龍・手机竖版清晰度测试 {index}",
        rarity="ウルトラ",
        model_number=f"TEST-JP{index:03d}",
        old_price=1000 + index * 10,
        new_price=1200 + index * 20,
        change_type="changed",
        price_diff=200 + index * 10,
        percent_diff=10.0 + index,
        changed_at="2026-07-23T00:00:00.000Z",
    )
    for index in range(1, 61)
]
renderer = DailyReportRenderer(
    SyntheticImageFetcher(),
    PlaywrightScreenshotBackend(),
)
originals = asyncio.run(
    renderer.render(changes, "2026-07-23")
)
compressed = asyncio.run(prepare_qq_pages(originals))

output_dir = (
    Path(tempfile.gettempdir())
    / "cardrush-portrait-smoke"
)
output_dir.mkdir(parents=True, exist_ok=True)
assert len(originals) == 2
for index, (original, qq_image) in enumerate(
    zip(originals, compressed),
    1,
):
    with Image.open(BytesIO(original)) as image:
        assert image.size == (1080, 1920)
    with Image.open(BytesIO(qq_image)) as image:
        assert image.size == (1080, 1920)
        assert image.format == "JPEG"
    assert len(qq_image) <= 450_000
    (output_dir / f"page-{index}.png").write_bytes(original)
    (output_dir / f"page-{index}.jpg").write_bytes(qq_image)
    print(
        f"page={index} original={len(original)} "
        f"qq={len(qq_image)}"
    )
print(output_dir)
'@ | .\.venv\Scripts\python.exe -
```

Expected: 生成 2 页；PNG 和 JPEG 都是 1080×1920；输出目录为系统临时目录下的
`cardrush-portrait-smoke`。

- [ ] **Step 4: 视觉检查首页和详情页**

使用图片查看工具依次打开：

```text
%TEMP%\cardrush-portrait-smoke\page-1.jpg
%TEMP%\cardrush-portrait-smoke\page-2.jpg
```

Expected:

- 首页统计面板、异动 TOP 25 和页码完整可见；
- 详情页正好显示 35 张，不裁切水印；
- 卡名最多两行，型号不挤压价格；
- 价格明显大于辅助信息；
- 文字边缘没有整图缩放造成的模糊；
- 页面没有横向或纵向溢出。

- [ ] **Step 5: 验证插件加载、差异和工作树**

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

Expected: 插件加载成功，`git diff --check` 无输出；工作树只保留任务开始前已经
存在的用户修改。特别是不得提交或覆盖
`docs/superpowers/specs/2026-07-23-cardrush-qq-image-compression-design.md`
当前已有的未提交修改。

- [ ] **Step 6: 提交验证修正（仅在需要时）**

如果视觉或全量验证暴露了本计划引入的问题，先补失败测试，再提交最小修正：

```powershell
git add hikari_bot/features/cardrush/reporting hikari_bot/plugins/monitors/cardrush_delivery.py tests/cardrush
git commit -m "test: verify Cardrush portrait reports"
```

如果没有修正，不创建空提交。
