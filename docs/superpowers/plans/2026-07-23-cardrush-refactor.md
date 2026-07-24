# Cardrush Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Cardrush 重构为可供机器人、上传接口和未来网站复用的独立核心，同时保持现有命令、调度、HTTP 接口和 SQLite 数据兼容。

**Architecture:** 新增 `hikari_bot.features.cardrush`，由 dataclass 模型、同步抓取客户端、同步 SQLite 仓储和统一异步 Service 组成；NoneBot、FastAPI、报表和 B 站发布保留为外围适配层。报表子模块统一手动图报、自动日报和 B 站发布的 HTML、卡图和 Playwright 渲染流程。

**Tech Stack:** Python 3.10+、NoneBot 2、FastAPI、requests、aiohttp、sqlite3、matplotlib、Playwright、pytest、pyflakes。

## Global Constraints

- 本次不新增网站路由、网站 API 或网页前端。
- 不改变 `data/cardrush_prices.db` 的文件路径、表名、字段、索引或已有数据。
- 不改变 QQ 命令名称、别名、权限、回复格式、15 分钟检查频率和 Asia/Tokyo 22:20 自动日报。
- 不改变 `/cr_upload` 的路径、鉴权请求头、请求体或响应体。
- 不更换 Cardrush 数据源、代理、超时或同步 requests 抓取行为。
- `hikari_bot/features/cardrush` 不得导入 NoneBot、FastAPI 或 OneBot 类型。
- 当前 B 站发布占位行为保持不变。
- 每个生产代码步骤必须先有一个因目标接口缺失或行为不满足而失败的测试。

## File Map

**Create**

- `hikari_bot/features/__init__.py`：功能包标识。
- `hikari_bot/features/cardrush/__init__.py`：Cardrush 公共导出及默认 Service 工厂。
- `hikari_bot/features/cardrush/models.py`：不可变价格 dataclass。
- `hikari_bot/features/cardrush/errors.py`：Cardrush 异常层次。
- `hikari_bot/features/cardrush/parsing.py`：参数、稀有度和卡名解析。
- `hikari_bot/features/cardrush/client.py`：Cardrush 页面请求和 `__NEXT_DATA__` 解析。
- `hikari_bot/features/cardrush/repository.py`：SQLite schema、写入和查询。
- `hikari_bot/features/cardrush/service.py`：统一异步应用接口。
- `hikari_bot/features/cardrush/reporting/__init__.py`：报表公共导出。
- `hikari_bot/features/cardrush/reporting/chart.py`：价格曲线 PNG。
- `hikari_bot/features/cardrush/reporting/text.py`：文字日报。
- `hikari_bot/features/cardrush/reporting/html.py`：日报 HTML。
- `hikari_bot/features/cardrush/reporting/renderer.py`：卡图下载和 Playwright 截图。
- `hikari_bot/features/cardrush/reporting/workflow.py`：日报查询及渲染编排。
- `hikari_bot/features/cardrush/reporting/templates/daily_report.css`：日报 CSS。
- `tests/cardrush/conftest.py`：Cardrush 测试 fixture。
- `tests/cardrush/fixtures/cardrush_page.html`：固定 Cardrush HTML。
- `tests/cardrush/test_models.py`
- `tests/cardrush/test_parsing.py`
- `tests/cardrush/test_client.py`
- `tests/cardrush/test_repository.py`
- `tests/cardrush/test_service.py`
- `tests/cardrush/test_text_report.py`
- `tests/cardrush/test_html_report.py`
- `tests/cardrush/test_report_workflow.py`
- `tests/cardrush/test_plugin_import.py`

**Modify**

- `hikari_bot/services/price.py`：缩为兼容 facade。
- `hikari_bot/plugins/monitors/cardrush.py`：缩为 NoneBot 适配层。
- `hikari_bot/plugins/web/routes/cr_upload.py`：改用异步 Service。

---

### Task 1: Cardrush 数据模型与异常契约

**Files:**

- Create: `hikari_bot/features/__init__.py`
- Create: `hikari_bot/features/cardrush/__init__.py`
- Create: `hikari_bot/features/cardrush/models.py`
- Create: `hikari_bot/features/cardrush/errors.py`
- Create: `tests/cardrush/test_models.py`

**Interfaces:**

- Produces: `PriceRecord.from_mapping(dict) -> PriceRecord`
- Produces: `PriceRecord.to_mapping() -> dict[str, object]`
- Produces: `PriceSnapshot`, `PricePoint`, `PriceChange`
- Produces: `CardrushError`, `CardrushClientError`, `CardrushRepositoryError`, `CardrushRenderError`

- [ ] **Step 1: Write the failing model contract tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from hikari_bot.features.cardrush.errors import (
    CardrushClientError,
    CardrushError,
    CardrushRenderError,
    CardrushRepositoryError,
)
from hikari_bot.features.cardrush.models import PriceRecord


def test_price_record_round_trips_mapping():
    raw = {
        "product_id": "42",
        "name": "青眼の白龍",
        "price": "3200",
        "rarity": "ウルトラ",
        "model_number": "QCAC-JP001",
        "updated_at": "2026-07-23T00:00:00.000Z",
    }

    record = PriceRecord.from_mapping(raw)

    assert record.product_id == 42
    assert record.price == 3200
    assert record.to_mapping() == {
        "product_id": 42,
        "name": "青眼の白龍",
        "price": 3200,
        "rarity": "ウルトラ",
        "model_number": "QCAC-JP001",
        "updated_at": "2026-07-23T00:00:00.000Z",
    }


def test_price_record_is_immutable():
    record = PriceRecord(1, "card", 100, None, None, None)
    with pytest.raises(FrozenInstanceError):
        record.price = 200


def test_specific_errors_share_cardrush_base():
    assert issubclass(CardrushClientError, CardrushError)
    assert issubclass(CardrushRepositoryError, CardrushError)
    assert issubclass(CardrushRenderError, CardrushError)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_models.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'hikari_bot.features'`.

- [ ] **Step 3: Implement immutable models and exceptions**

Create `models.py` with these exact public types:

```python
from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PriceRecord:
    product_id: int
    name: str
    price: int
    rarity: str | None
    model_number: str | None
    updated_at: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PriceRecord":
        return cls(
            product_id=int(value["product_id"]),
            name=str(value["name"]),
            price=int(value["price"]),
            rarity=value.get("rarity"),
            model_number=value.get("model_number"),
            updated_at=value.get("updated_at"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriceSnapshot:
    product_id: int
    name: str
    rarity: str | None
    model_number: str | None
    price: int
    changed_at: str


@dataclass(frozen=True)
class PricePoint:
    price: int
    changed_at: str


@dataclass(frozen=True)
class PriceChange:
    product_id: int
    name: str
    rarity: str | None
    model_number: str | None
    old_price: int | None
    new_price: int
    change_type: str
    price_diff: int | None
    percent_diff: float | None
    changed_at: str
```

Create `errors.py`:

```python
class CardrushError(Exception):
    """Cardrush 功能的基础异常。"""


class CardrushClientError(CardrushError):
    """Cardrush 请求或页面解析失败。"""


class CardrushRepositoryError(CardrushError):
    """Cardrush 持久化失败。"""


class CardrushRenderError(CardrushError):
    """Cardrush 报表渲染失败。"""
```

Have `hikari_bot/features/cardrush/__init__.py` re-export only the models and exceptions at this stage.

- [ ] **Step 4: Run the model tests and verify GREEN**

Run:

```powershell
python -m pytest tests/cardrush/test_models.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```powershell
git add hikari_bot/features tests/cardrush/test_models.py
git commit -m "refactor: add Cardrush domain models"
```

---

### Task 2: 提取参数解析和 Cardrush 客户端

**Files:**

- Create: `hikari_bot/features/cardrush/parsing.py`
- Create: `hikari_bot/features/cardrush/client.py`
- Create: `tests/cardrush/fixtures/cardrush_page.html`
- Create: `tests/cardrush/test_parsing.py`
- Create: `tests/cardrush/test_client.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py:54-180`
- Modify: `hikari_bot/services/price.py:1-95`

**Interfaces:**

- Produces: `rarity_jp_to_en(str) -> str`
- Produces: `expand_rarity_to_jp_list(str) -> list[str]`
- Produces: `clean_card_name(str) -> str`
- Produces: `parse_price_query(str) -> tuple[str, str | None, str | None]`
- Produces: `resolve_card_name_jp(str) -> Awaitable[str]`
- Produces: `CardrushClient.extract_records(str) -> list[PriceRecord]`
- Produces: `CardrushClient.query(...) -> list[PriceRecord]`
- Produces: `CardrushClient.query_all() -> list[PriceRecord]`

- [ ] **Step 1: Write failing parsing tests against the new module**

```python
from hikari_bot.features.cardrush.parsing import (
    clean_card_name,
    expand_rarity_to_jp_list,
    parse_price_query,
    rarity_jp_to_en,
)


def test_parse_price_query_preserves_existing_filters():
    assert parse_price_query("青眼白龙 ALIN UR") == ("青眼白龙", "UR", "ALIN")
    assert parse_price_query("Blue-Eyes UR ALIN") == ("Blue-Eyes", "UR", "ALIN")
    assert parse_price_query("青眼白龙") == ("青眼白龙", None, None)


def test_rarity_prefix_expands_all_matching_japanese_names():
    values = expand_rarity_to_jp_list("PSER")
    assert "プリズマティックシークレット" in values
    assert "OFプリズマティックシークレット" in values


def test_name_cleanup_and_rarity_display_match_current_behavior():
    assert clean_card_name("＜青眼・白龍＞") == "青眼白龍"
    assert rarity_jp_to_en("ウルトラ") == "UR"
    assert rarity_jp_to_en("未知レア") == "未知レア"
```

- [ ] **Step 2: Write a fixed HTML client test**

Create `tests/cardrush/fixtures/cardrush_page.html`:

```html
<!doctype html>
<html>
<body>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"buyingPrices":[{"yugioh_ocha_product_id":101,"name":"青眼の白龍","amount":3200,"rarity":"ウルトラ","model_number":"QCAC-JP001","updated_at":"2026-07-23T00:00:00.000Z"},{"yugioh_ocha_product_id":102,"name":"真紅眼の黒竜","amount":2400,"rarity":"シークレット","model_number":"QCAC-JP002","updated_at":"2026-07-23T00:00:00.000Z"}]}}}</script>
</body>
</html>
```

Then add:

```python
from pathlib import Path

import pytest

from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.errors import CardrushClientError


def test_extract_data_returns_typed_price_records():
    html = Path("tests/cardrush/fixtures/cardrush_page.html").read_text(encoding="utf-8")
    records = CardrushClient.extract_records(html)

    assert [record.product_id for record in records] == [101, 102]
    assert records[0].name == "青眼の白龍"
    assert records[0].price == 3200


def test_extract_data_wraps_missing_next_data():
    with pytest.raises(CardrushClientError, match="__NEXT_DATA__"):
        CardrushClient.extract_records("<html></html>")
```

- [ ] **Step 3: Run both files and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_parsing.py tests/cardrush/test_client.py -q
```

Expected: imports fail because `parsing.py` and `client.py` do not exist.

- [ ] **Step 4: Move parsing behavior without changing outputs**

Move `RARITY_MAPPING`, `rarity_jp_to_en`, `expand_rarity_to_jp_list`, `clean_card_name`, `parse_price_query`, and `resolve_card_name_jp` from `plugins/monitors/cardrush.py` into `features/cardrush/parsing.py`. Keep the existing regexes and mapping values byte-for-byte except for imports. Replace the plugin definitions with explicit imports from the new module.

The new module must start with:

```python
import re

from hikari_bot.services.ygocard import get_card_info
```

and must not import NoneBot.

- [ ] **Step 5: Implement the isolated client**

Move the current request settings and pagination behavior from `services/price.py` into:

```python
import json
import re
from typing import Any

import requests

from hikari_bot.features.cardrush.errors import CardrushClientError
from hikari_bot.features.cardrush.models import PriceRecord


class CardrushClient:
    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        proxies: dict[str, str] | None,
        timeout: int = 30,
    ) -> None:
        self.url = url
        self.headers = headers
        self.proxies = proxies
        self.timeout = timeout

    @staticmethod
    def extract_records(html: str) -> list[PriceRecord]:
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            raise CardrushClientError("无法找到 __NEXT_DATA__")
        try:
            data: dict[str, Any] = json.loads(match.group(1))
            prices = data["props"]["pageProps"].get("buyingPrices", [])
            return [
                PriceRecord(
                    product_id=int(item["yugioh_ocha_product_id"]),
                    name=str(item["name"]),
                    price=int(item["amount"]),
                    rarity=item.get("rarity"),
                    model_number=item.get("model_number"),
                    updated_at=item.get("updated_at"),
                )
                for item in prices
                if item.get("yugioh_ocha_product_id")
                and item.get("name")
                and item.get("amount") is not None
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CardrushClientError(f"Cardrush 页面数据解析失败: {exc}") from exc

    def query(
        self,
        name: str | None = None,
        rarity: str | None = None,
        model_number: str | None = None,
        limit: int = 100,
        page: int | None = None,
    ) -> list[PriceRecord]:
        params: dict[str, object] = {"limit": limit}
        if name:
            params["name"] = name
        if rarity:
            params["rarity"] = rarity
        if model_number:
            params["model_number"] = model_number
        if page is not None:
            params["page"] = page
        try:
            response = requests.get(
                self.url,
                params=params,
                headers=self.headers,
                proxies=self.proxies,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self.extract_records(response.text)
        except CardrushClientError:
            raise
        except requests.RequestException as exc:
            raise CardrushClientError(f"Cardrush request failed: {exc}") from exc

    def query_all(self) -> list[PriceRecord]:
        records: list[PriceRecord] = []
        page = 1
        page_size = 500
        while True:
            current = self.query(limit=page_size, page=page)
            records.extend(current)
            if len(current) < page_size:
                return records
            page += 1
```

- [ ] **Step 6: Run parsing and client tests**

Run:

```powershell
python -m pytest tests/cardrush/test_parsing.py tests/cardrush/test_client.py -q
python -m pyflakes hikari_bot/features/cardrush/parsing.py hikari_bot/features/cardrush/client.py
```

Expected: all tests pass and pyflakes emits no findings.

- [ ] **Step 7: Commit**

```powershell
git add hikari_bot/features/cardrush hikari_bot/plugins/monitors/cardrush.py tests/cardrush
git commit -m "refactor: extract Cardrush parsing and client"
```

---

### Task 3: 提取 SQLite Repository 并锁定 schema

**Files:**

- Create: `hikari_bot/features/cardrush/repository.py`
- Create: `tests/cardrush/conftest.py`
- Create: `tests/cardrush/test_repository.py`
- Modify: `hikari_bot/services/price.py:97-534`

**Interfaces:**

- Consumes: `PriceRecord`, `PriceSnapshot`, `PricePoint`, `PriceChange`
- Produces: `PriceRepository.initialize() -> None`
- Produces: `PriceRepository.reset() -> None`
- Produces: `PriceRepository.save_prices(Sequence[PriceRecord]) -> int`
- Produces: `PriceRepository.search_latest(...) -> list[PriceSnapshot]`
- Produces: `PriceRepository.get_history(int) -> list[PricePoint]`
- Produces: `PriceRepository.get_daily_changes(...) -> list[PriceChange]`

- [ ] **Step 1: Write failing repository tests using a temporary database**

```python
import sqlite3

from hikari_bot.features.cardrush.models import PriceRecord
from hikari_bot.features.cardrush.repository import PriceRepository


def record(product_id: int, price: int, updated_at: str) -> PriceRecord:
    return PriceRecord(
        product_id=product_id,
        name=f"card-{product_id}",
        price=price,
        rarity="ウルトラ",
        model_number="TEST-JP001",
        updated_at=updated_at,
    )


def test_initialize_preserves_current_schema(tmp_path):
    db_path = tmp_path / "prices.db"
    repository = PriceRepository(db_path)
    repository.initialize()

    with sqlite3.connect(db_path) as connection:
        schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name IN ("
            "'card_price_history', "
            "'idx_card_price_history_product_time', "
            "'idx_card_price_history_changed_at'"
            ") ORDER BY type, name"
        ).fetchall()

    assert len(schema) == 3
    assert "product_id INTEGER NOT NULL" in schema[-1][2]
    assert "changed_at TEXT NOT NULL" in schema[-1][2]


def test_save_records_only_on_change_and_marks_missing_as_zero(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    first = [
        record(1, 1000, "2026-07-22T00:00:00.000Z"),
        record(2, 2000, "2026-07-22T00:00:00.000Z"),
    ]
    assert repository.save_prices(first) == 2
    assert repository.save_prices(first) == 0

    second = [record(1, 1500, "2026-07-23T00:00:00.000Z")]
    assert repository.save_prices(second) == 2

    assert [point.price for point in repository.get_history(1)] == [1000, 1500]
    assert repository.get_history(2)[-1].price == 0


def test_search_and_daily_changes_return_models(tmp_path):
    repository = PriceRepository(tmp_path / "prices.db")
    repository.save_prices([record(1, 1000, "2026-07-22T00:00:00.000Z")])
    repository.save_prices([record(1, 1500, "2026-07-23T00:00:00.000Z")])

    result = repository.search_latest("card-1")
    changes = repository.get_daily_changes("2026-07-23")

    assert result[0].price == 1500
    assert changes[0].old_price == 1000
    assert changes[0].new_price == 1500
    assert changes[0].price_diff == 500
```

- [ ] **Step 2: Run repository tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_repository.py -q
```

Expected: import fails because `repository.py` does not exist.

- [ ] **Step 3: Implement repository by moving existing SQL unchanged**

Create `PriceRepository` with `Path | str` constructor and one connection per public method. Move these exact SQL responsibilities from `services/price.py`:

- `init_database` → `initialize`
- `reset_database` → `reset`
- `get_latest_price` → private `_get_latest_price`
- `save_prices` → `save_prices`
- `_build_series_where` → private module helper
- `search_local_prices` → `search_latest`
- `get_price_history` → `get_history`
- `get_daily_report_changes` → `get_daily_changes`
- `get_series_latest_prices` → `get_series_latest`

Every public method must use this error boundary:

```python
try:
    with sqlite3.connect(self.db_path) as connection:
        ...
except sqlite3.Error as exc:
    raise CardrushRepositoryError(
        f"Cardrush database operation failed: {exc}"
    ) from exc
```

Convert input dataclasses to SQL parameters and SQL rows to dataclasses at the repository boundary. Preserve the existing `changed_at` ordering, price range `500..100000`, `price=0` disappearance record, series filter and exclusion-prefix behavior.

- [ ] **Step 4: Run repository tests and inspect schema**

Run:

```powershell
python -m pytest tests/cardrush/test_repository.py -q
python -m pyflakes hikari_bot/features/cardrush/repository.py
```

Expected: all repository tests pass and no static findings.

- [ ] **Step 5: Commit**

```powershell
git add hikari_bot/features/cardrush/repository.py tests/cardrush
git commit -m "refactor: extract Cardrush price repository"
```

---

### Task 4: 增加异步 Service 和旧模块兼容 facade

**Files:**

- Create: `hikari_bot/features/cardrush/service.py`
- Create: `tests/cardrush/test_service.py`
- Modify: `hikari_bot/features/cardrush/__init__.py`
- Modify: `hikari_bot/services/price.py`

**Interfaces:**

- Consumes: `CardrushClient`, `PriceRepository`
- Produces: `CardrushService.search_prices(...)`
- Produces: `CardrushService.get_price_history(...)`
- Produces: `CardrushService.get_daily_changes(...)`
- Produces: `CardrushService.save_prices(...)`
- Produces: `CardrushService.refresh_prices()`
- Produces: `get_default_cardrush_service() -> CardrushService`
- Preserves: legacy functions exported from `hikari_bot.services.price`

- [ ] **Step 1: Write failing async service tests**

Use `asyncio.run` so the suite does not require `pytest-asyncio`:

```python
import asyncio

from hikari_bot.features.cardrush.models import PricePoint, PriceRecord, PriceSnapshot
from hikari_bot.features.cardrush.service import CardrushService


class FakeClient:
    def query_all(self):
        return [PriceRecord(1, "card", 1000, None, None, "2026-07-23T00:00:00.000Z")]


class FakeRepository:
    def __init__(self):
        self.saved = []

    def search_latest(self, name, rarity=None, model_number=None, limit=10):
        return [PriceSnapshot(1, name, rarity, model_number, 1000, "2026-07-23")]

    def get_history(self, product_id):
        return [PricePoint(900, "2026-07-22"), PricePoint(1000, "2026-07-23")]

    def get_daily_changes(self, date, series_keywords=None, min_abs_diff=0,
                          include_new=True, exclude_prefixes=None):
        return []

    def save_prices(self, records):
        self.saved = list(records)
        return len(self.saved)

    def reset(self):
        return None


def test_service_exposes_async_search_and_history():
    service = CardrushService(FakeRepository(), FakeClient())
    result = asyncio.run(service.search_prices("青眼の白龍", rarity="ウルトラ"))
    history = asyncio.run(service.get_price_history(1))
    assert result[0].name == "青眼の白龍"
    assert result[0].rarity == "ウルトラ"
    assert [point.price for point in history] == [900, 1000]


def test_refresh_fetches_and_saves_records():
    repository = FakeRepository()
    service = CardrushService(repository, FakeClient())
    assert asyncio.run(service.refresh_prices()) == 1
    assert repository.saved[0].product_id == 1
```

- [ ] **Step 2: Run service tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_service.py -q
```

Expected: import fails because `service.py` does not exist.

- [ ] **Step 3: Implement the async service**

```python
import asyncio
from collections.abc import Sequence

from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.models import (
    PriceChange,
    PricePoint,
    PriceRecord,
    PriceSnapshot,
)
from hikari_bot.features.cardrush.repository import PriceRepository


class CardrushService:
    def __init__(
        self,
        repository: PriceRepository,
        client: CardrushClient | None = None,
    ) -> None:
        self.repository = repository
        self.client = client

    async def search_prices(
        self,
        name: str,
        rarity: str | list[str] | None = None,
        model_number: str | None = None,
        limit: int = 10,
    ) -> list[PriceSnapshot]:
        return await asyncio.to_thread(
            self.repository.search_latest,
            name,
            rarity,
            model_number,
            limit,
        )

    async def get_price_history(self, product_id: int) -> list[PricePoint]:
        return await asyncio.to_thread(self.repository.get_history, product_id)

    async def get_daily_changes(
        self,
        date: str,
        *,
        series_keywords: list[str] | None = None,
        min_abs_diff: int = 0,
        include_new: bool = True,
        exclude_prefixes: list[str] | None = None,
    ) -> list[PriceChange]:
        return await asyncio.to_thread(
            self.repository.get_daily_changes,
            date,
            series_keywords,
            min_abs_diff,
            include_new,
            exclude_prefixes,
        )

    async def save_prices(self, records: Sequence[PriceRecord]) -> int:
        return await asyncio.to_thread(self.repository.save_prices, records)

    async def refresh_prices(self) -> int:
        if self.client is None:
            raise RuntimeError("Cardrush client is not configured")
        records = await asyncio.to_thread(self.client.query_all)
        return await self.save_prices(records)

    async def reset_database(self) -> None:
        await asyncio.to_thread(self.repository.reset)
```

- [ ] **Step 4: Add lazy default wiring and compatibility functions**

In `features/cardrush/__init__.py`, add a cached factory using the existing `settings.cardrush_proxies`, URL, headers and original `DB_PATH`:

```python
import os
from functools import lru_cache

from hikari_bot.core.config import settings
from hikari_bot.core.constants import DATA_DIR
from hikari_bot.features.cardrush.client import CardrushClient
from hikari_bot.features.cardrush.repository import PriceRepository
from hikari_bot.features.cardrush.service import CardrushService


@lru_cache(maxsize=1)
def get_default_cardrush_service() -> CardrushService:
    repository = PriceRepository(os.path.join(DATA_DIR, "cardrush_prices.db"))
    client = CardrushClient(
        url="https://cardrush.media/yugioh/buying_prices",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
        proxies=settings.cardrush_proxies,
        timeout=30,
    )
    return CardrushService(repository, client)
```

Replace `services/price.py` with a facade whose public synchronous functions call the default service's repository/client directly and convert dataclasses back to mappings. Preserve these legacy names:

```text
query
query_all
init_database
reset_database
save_prices
get_daily_report_changes
get_series_latest_prices
search_local_prices
get_price_history
split_changes
format_change_line
build_daily_report_text
```

`save_prices` must accept both dictionaries and `PriceRecord` instances:

```python
def save_prices(prices_data):
    records = [
        value if isinstance(value, PriceRecord) else PriceRecord.from_mapping(value)
        for value in prices_data
    ]
    return get_default_cardrush_service().repository.save_prices(records)
```

- [ ] **Step 5: Run service and existing import checks**

Run:

```powershell
python -m pytest tests/cardrush/test_service.py tests/cardrush/test_repository.py -q
python -c "from hikari_bot.services.price import save_prices, search_local_prices"
python -m pyflakes hikari_bot/features/cardrush hikari_bot/services/price.py
```

Expected: tests pass, legacy imports succeed and pyflakes emits no findings.

- [ ] **Step 6: Commit**

```powershell
git add hikari_bot/features/cardrush hikari_bot/services/price.py tests/cardrush
git commit -m "refactor: add asynchronous Cardrush service"
```

---

### Task 5: 提取纯报表、曲线和 CSS

**Files:**

- Create: `hikari_bot/features/cardrush/reporting/__init__.py`
- Create: `hikari_bot/features/cardrush/reporting/chart.py`
- Create: `hikari_bot/features/cardrush/reporting/text.py`
- Create: `hikari_bot/features/cardrush/reporting/html.py`
- Create: `hikari_bot/features/cardrush/reporting/templates/daily_report.css`
- Create: `tests/cardrush/test_text_report.py`
- Create: `tests/cardrush/test_html_report.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py:182-973`

**Interfaces:**

- Produces: `draw_price_chart(Sequence[PricePoint]) -> bytes`
- Produces: `parse_date_arg(str, today: date | None = None) -> str`
- Produces: `format_daily_report(Sequence[PriceChange], str) -> list[str]`
- Produces: `render_daily_report_html(...) -> list[str]`

- [ ] **Step 1: Write failing text report tests**

```python
from datetime import date

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.text import (
    format_daily_report,
    parse_date_arg,
)


def change(kind: str, old: int | None, new: int) -> PriceChange:
    diff = None if old is None else new - old
    return PriceChange(
        1, "青眼の白龍", "ウルトラ", "QCAC-JP001",
        old, new, kind, diff,
        None if old in (None, 0) else diff / old * 100,
        "2026-07-23T00:00:00.000Z",
    )


def test_parse_date_arg_uses_previous_year_for_future_month_day():
    assert parse_date_arg("12.31", today=date(2026, 7, 23)) == "2025-12-31"


def test_text_report_preserves_summary_and_categories():
    messages = format_daily_report(
        [change("changed", 1000, 1500), change("new", None, 2000)],
        "2026-07-23",
    )
    joined = "\n".join(messages)
    assert "共 2 条变化" in joined
    assert "涨价 1" in joined
    assert "新增 1" in joined
    assert "青眼の白龍" in joined
```

- [ ] **Step 2: Write failing HTML report tests**

```python
from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.html import render_daily_report_html


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
        for index in range(1, 18)
    ]

    pages = render_daily_report_html(changes, "2026-07-23", image_map={})

    assert len(pages) >= 2
    assert "&lt;card-1&gt;" in "\n".join(pages)
    assert "PAGE 1/" in pages[0]
```

- [ ] **Step 3: Run report tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_text_report.py tests/cardrush/test_html_report.py -q
```

Expected: imports fail because reporting modules do not exist.

- [ ] **Step 4: Extract pure report behavior**

Move these exact existing behaviors:

- `_draw_price_chart` → `reporting/chart.py`, accepting `PricePoint` values instead of dictionaries.
- `_parse_date_arg` and `_format_daily_report` → `reporting/text.py`.
- `_load_bg_image_b64`, `_card_html`, `_overview_score`, `_make_page_html`, `_render_daily_report_html` → `reporting/html.py`.
- `_build_html_css` CSS body → `reporting/templates/daily_report.css`.

`html.py` must load CSS through `Path(__file__).with_name("templates") / "daily_report.css"` and substitute only the existing background URL placeholder. Card names, rarity and model number must pass through `html.escape`.

Keep the current page size, ranking formula, colors, dimensions, overview page and placeholders unchanged.

- [ ] **Step 5: Run pure report tests and static checks**

Run:

```powershell
python -m pytest tests/cardrush/test_text_report.py tests/cardrush/test_html_report.py -q
python -m pyflakes hikari_bot/features/cardrush/reporting
```

Expected: report tests pass and no static findings.

- [ ] **Step 6: Commit**

```powershell
git add hikari_bot/features/cardrush/reporting tests/cardrush
git commit -m "refactor: extract Cardrush report formatting"
```

---

### Task 6: 合并卡图下载、Playwright 渲染和日报 Workflow

**Files:**

- Create: `hikari_bot/features/cardrush/reporting/renderer.py`
- Create: `hikari_bot/features/cardrush/reporting/workflow.py`
- Create: `tests/cardrush/test_report_workflow.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py:975-1055,1198-1326`

**Interfaces:**

- Produces: `DailyReportRenderer.render(changes, date) -> list[bytes]`
- Produces: `DailyReportWorkflow.render_for_date(date) -> list[bytes]`
- Guarantees: every invocation uses an independent `TemporaryDirectory`

- [ ] **Step 1: Write a failing workflow test with an injected renderer**

```python
import asyncio

from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.workflow import DailyReportWorkflow


class FakeService:
    async def get_daily_changes(self, date, **kwargs):
        return [
            PriceChange(
                1, "card", None, None, 1000, 1200,
                "changed", 200, 20.0, f"{date}T00:00:00.000Z",
            )
        ]


class FakeRenderer:
    def __init__(self):
        self.received = None

    async def render(self, changes, date):
        self.received = (changes, date)
        return [b"page-1", b"page-2"]


def test_workflow_queries_changes_and_returns_renderer_pages():
    renderer = FakeRenderer()
    workflow = DailyReportWorkflow(FakeService(), renderer)

    pages = asyncio.run(workflow.render_for_date("2026-07-23"))

    assert pages == [b"page-1", b"page-2"]
    assert renderer.received[1] == "2026-07-23"
```

- [ ] **Step 2: Write a failing renderer cleanup test**

Expose no test-only production API. Add this test using injected collaborators and a recorded temporary directory:

```python
import asyncio
import shutil
from pathlib import Path

import pytest

import hikari_bot.features.cardrush.reporting.renderer as renderer_module
from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.models import PriceChange
from hikari_bot.features.cardrush.reporting.renderer import DailyReportRenderer


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
        1, "card", None, None, 1000, 1200,
        "changed", 200, 20.0, "2026-07-23T00:00:00.000Z",
    )


def test_renderer_cleans_temporary_directory_after_success(tmp_path, monkeypatch):
    work_dir = tmp_path / "render"
    monkeypatch.setattr(
        renderer_module.tempfile,
        "TemporaryDirectory",
        lambda prefix: RecordedTemporaryDirectory(work_dir),
    )
    renderer = DailyReportRenderer(FakeImageFetcher(), FakeScreenshotBackend())

    pages = asyncio.run(renderer.render([sample_change()], "2026-07-23"))

    assert pages[0].startswith(b"\x89PNG")
    assert not work_dir.exists()


def test_renderer_cleans_temporary_directory_after_failure(tmp_path, monkeypatch):
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
        asyncio.run(renderer.render([sample_change()], "2026-07-23"))

    assert not work_dir.exists()
```

The production constructor must support:

```python
DailyReportRenderer(
    image_fetcher: CardImageFetcher | None = None,
    screenshot_backend: ScreenshotBackend | None = None,
)
```

where protocols are:

```python
class CardImageFetcher(Protocol):
    async def fetch(
        self,
        changes: Sequence[PriceChange],
        image_dir: Path,
    ) -> dict[int, str]: ...


class ScreenshotBackend(Protocol):
    async def capture(self, html_pages: Sequence[str], work_dir: Path) -> list[bytes]: ...
```

- [ ] **Step 3: Run workflow tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_report_workflow.py -q
```

Expected: import fails because `workflow.py` and `renderer.py` do not exist.

- [ ] **Step 4: Implement renderer adapters and cleanup**

Move `_fetch_card_images` into `AiohttpCardImageFetcher.fetch`, retaining URL, concurrency, timeout, retries and unknown-card fallback.

Move Playwright code into `PlaywrightScreenshotBackend.capture`:

```python
async with async_playwright() as playwright:
    browser = await playwright.chromium.launch()
    try:
        for index, html_page in enumerate(html_pages, 1):
            html_path = work_dir / f"page-{index}.html"
            html_path.write_text(html_page, encoding="utf-8")
            page = await browser.new_page(viewport={"width": 1340, "height": 900})
            try:
                page.set_default_timeout(120_000)
                await page.goto(html_path.as_uri(), wait_until="domcontentloaded")
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
```

`DailyReportRenderer.render` must:

```python
async def render(self, changes, date):
    try:
        with tempfile.TemporaryDirectory(prefix="cardrush-report-") as temp:
            work_dir = Path(temp)
            image_dir = work_dir / "images"
            image_dir.mkdir()
            image_map = await self.image_fetcher.fetch(changes, image_dir)
            html_pages = render_daily_report_html(changes, date, image_map)
            return await self.screenshot_backend.capture(html_pages, work_dir)
    except CardrushRenderError:
        raise
    except Exception as exc:
        raise CardrushRenderError(f"Cardrush report rendering failed: {exc}") from exc
```

- [ ] **Step 5: Implement the workflow**

```python
class DailyReportWorkflow:
    def __init__(self, service, renderer):
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
```

- [ ] **Step 6: Run workflow and report tests**

Run:

```powershell
python -m pytest tests/cardrush/test_report_workflow.py tests/cardrush/test_html_report.py -q
python -m pyflakes hikari_bot/features/cardrush/reporting
```

Expected: all tests pass and no static findings.

- [ ] **Step 7: Commit**

```powershell
git add hikari_bot/features/cardrush/reporting tests/cardrush
git commit -m "refactor: unify Cardrush report rendering"
```

---

### Task 7: 将 NoneBot 插件和上传路由改为薄适配层

**Files:**

- Create: `tests/cardrush/test_plugin_import.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py`
- Modify: `hikari_bot/plugins/web/routes/cr_upload.py`

**Interfaces:**

- Consumes: `get_default_cardrush_service`, parsing functions, report modules and workflow.
- Preserves: all matcher names, aliases, permissions and scheduler decorators.
- Preserves: `/cr_upload` validation and response mapping.

- [ ] **Step 1: Write failing architectural and registration tests**

```python
import ast
from pathlib import Path


def test_cardrush_core_has_no_framework_imports():
    root = Path("hikari_bot/features/cardrush")
    forbidden = {"nonebot", "fastapi", "nonebot_plugin_apscheduler"}
    imported = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(forbidden)


def test_plugin_keeps_command_and_schedule_declarations():
    source = Path("hikari_bot/plugins/monitors/cardrush.py").read_text(encoding="utf-8")
    for command in (
        '"卡价查询"',
        '"卡价曲线"',
        '"卡价图报"',
        '"卡价日报"',
        '"重置卡价数据库"',
    ):
        assert command in source
    assert 'minutes=15' in source
    assert 'hour=22, minute=20, timezone="Asia/Tokyo"' in source
    assert len(source.splitlines()) < 450
```

Add a direct route test with the Service dependency patched:

```python
import asyncio
import importlib


def test_upload_route_preserves_response_shape(monkeypatch):
    module = importlib.import_module(
        "hikari_bot.plugins.web.routes.cr_upload"
    )

    class FakeService:
        async def save_prices(self, records):
            assert len(records) == 1
            return 1

    monkeypatch.setattr(module, "service", FakeService())
    payload = module.UploadPayload(
        prices=[
            module.PriceRecord(
                product_id=1,
                name="青眼の白龍",
                price=3200,
                rarity="ウルトラ",
                model_number="QCAC-JP001",
                updated_at="2026-07-23T00:00:00.000Z",
            )
        ]
    )

    response = asyncio.run(module.cr_upload(payload))

    assert response == {"ok": True, "received": 1, "saved": 1}
```

- [ ] **Step 2: Run the adapter tests and verify RED**

Run:

```powershell
python -m pytest tests/cardrush/test_plugin_import.py -q
```

Expected: the architecture assertion fails while large portions of Cardrush business/report code still live in the plugin or imports do not yet use the final Service.

- [ ] **Step 3: Replace plugin internals with new module calls**

Keep every matcher declaration and decorator unchanged. Update handler bodies as follows:

- Card price: parse with `parse_price_query`, resolve name, `await service.search_prices`, format the same lines.
- Price curve: `await service.search_prices`, `await service.get_price_history`, then `draw_price_chart`.
- Text report: `await service.get_daily_changes`, then `format_daily_report`.
- Image report: call one shared `DailyReportWorkflow.render_for_date` and send returned bytes.
- Price check: call `await service.refresh_prices`.
- Reset: call `await service.reset_database`.
- Automatic report and B 站 command: call the same workflow; only destination behavior remains in the plugin.

Instantiate dependencies once at module import:

```python
service = get_default_cardrush_service()
report_renderer = DailyReportRenderer()
report_workflow = DailyReportWorkflow(service, report_renderer)
```

Remove all copied SQL, requests, CSS, HTML building, image downloading and Playwright implementation from the plugin.

- [ ] **Step 4: Change `/cr_upload` to call the async Service**

Replace the executor wrapper with:

```python
service = get_default_cardrush_service()


@router.post("/cr_upload", dependencies=[Depends(verify_api_key)])
async def cr_upload(payload: UploadPayload):
    records = [PriceRecord.from_mapping(record.model_dump()) for record in payload.prices]
    try:
        saved = await service.save_prices(records)
    except CardrushError as exc:
        await log_message(f"[cr_upload] save_prices failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if saved > 0:
        await log_message(f"[cr_upload] Finish checking with {saved} change(s).")
    return {"ok": True, "received": len(records), "saved": saved}
```

Remove now-unused `asyncio`, `functools`, legacy `save_prices` and `os` imports.

- [ ] **Step 5: Run adapter and full Cardrush tests**

Run:

```powershell
python -m pytest tests/cardrush -q
python -m pyflakes hikari_bot/features/cardrush hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/web/routes/cr_upload.py
```

Expected: all Cardrush tests pass and pyflakes emits no findings.

- [ ] **Step 6: Commit**

```powershell
git add hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/web/routes/cr_upload.py tests/cardrush
git commit -m "refactor: make Cardrush adapters thin"
```

---

### Task 8: 全量兼容验证和真实渲染冒烟测试

**Files:**

- Modify only files required to fix failures introduced by Tasks 1–7.
- Do not add new features or change validated public behavior.

**Interfaces:**

- Verifies all design acceptance criteria.

- [ ] **Step 1: Run the complete local test suite**

```powershell
python -m pytest -q
```

Expected: all tests pass with no collection errors.

- [ ] **Step 2: Run syntax and static verification**

```powershell
python -m compileall -q bot.py hikari_bot scripts
python -m pyflakes bot.py hikari_bot scripts
```

Expected: compileall exits `0`; no new Cardrush findings. Existing unrelated findings must be listed separately and not hidden.

- [ ] **Step 3: Compare production database schema without modifying it**

Run a read-only script that captures:

```sql
SELECT type, name, sql
FROM sqlite_master
WHERE name IN (
  'card_price_history',
  'idx_card_price_history_product_time',
  'idx_card_price_history_changed_at'
)
ORDER BY type, name;
```

Compare it with the schema asserted in `test_repository.py`. Do not call `reset_database` against the production path.

- [ ] **Step 4: Run a real Playwright one-page smoke render**

Use one in-memory `PriceChange`, invoke `DailyReportRenderer.render`, and write the returned bytes only into a newly created temporary directory. Verify:

```text
page count >= 1
every page begins with PNG signature bytes
temporary render directory is absent after completion
DATA_DIR/card_images was not created
DATA_DIR/daily_report_html was not created
```

If Playwright Chromium is unavailable, install the project-declared browser runtime before rerunning; do not weaken or skip the renderer test.

- [ ] **Step 5: Verify the working tree and diff**

```powershell
git status --short
git diff --check
git diff --stat HEAD~7..HEAD
```

Expected: only intended Cardrush modules, tests and documentation changed; no generated database, HTML, image, log or cache files are staged.

- [ ] **Step 6: Commit verification-only corrections, if any**

When verification required code corrections, stage only those corrections and commit:

```powershell
git add hikari_bot/features/cardrush hikari_bot/services/price.py hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/web/routes/cr_upload.py tests/cardrush
git commit -m "test: complete Cardrush refactor verification"
```

If no corrections were required, do not create an empty commit.
