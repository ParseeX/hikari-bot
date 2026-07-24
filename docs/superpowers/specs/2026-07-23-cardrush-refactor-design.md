# Cardrush 模块重构设计

## 背景

Cardrush 功能目前主要分布在：

- `hikari_bot/plugins/monitors/cardrush.py`：NoneBot 命令、参数解析、价格曲线、文字日报、HTML 日报、卡图下载、Playwright 截图、定时任务、管理员通知和 B 站发布。
- `hikari_bot/services/price.py`：Cardrush 页面抓取、SQLite 建表和读写、价格查询、历史查询及日报数据计算。

这些职责相互耦合，导致机器人插件和价格服务文件过大，手动图报、自动图报与 B 站发布之间存在重复渲染流程，临时目录也存在并发清理风险。

项目后续计划将卡价查询发布为网站。网站上线后，手动卡价日报和卡价曲线等 QQ 指令可能被删除。因此本次重构需要将 Cardrush 建设为不依赖 NoneBot 或 FastAPI 的独立核心，机器人、定时任务、上传接口和未来网站都作为外围适配层。

本次不实现 Cardrush 网站、网站 API 或网页前端。

## 目标

1. 将 Cardrush 网络访问、SQLite 存储、业务查询、报表渲染和机器人接入拆成职责清晰的模块。
2. 建立可被未来网站复用的异步 `CardrushService`。
3. 保留现有命令、输出格式、权限、定时频率、上传接口和数据库结构。
4. 合并三套重复的日报渲染流程。
5. 让日报和价格曲线成为可选模块，未来删除时不影响价格核心。
6. 用自动化测试保护现有行为和历史数据兼容性。

## 非目标

- 不新增网站路由、JSON 查询 API 或网页页面。
- 不更换 Cardrush 数据源、代理策略或抓取协议。
- 不迁移 SQLite 数据库，不改变表名、字段或历史记录。
- 不实现当前占位的 B 站发布能力。
- 不改变现有 QQ 命令名称、别名、权限、回复格式和调度时间。
- 不引入 PostgreSQL、任务队列或新的持久化技术。

## 总体架构

目标目录：

```text
hikari_bot/features/cardrush/
  __init__.py
  models.py
  errors.py
  client.py
  repository.py
  service.py
  parsing.py

  reporting/
    __init__.py
    chart.py
    text.py
    html.py
    renderer.py
    workflow.py
    templates/
      daily_report.css

hikari_bot/plugins/monitors/cardrush.py
hikari_bot/services/price.py
```

依赖方向：

```text
QQ 插件 ───────┐
定时任务 ──────┼──→ CardrushService → PriceRepository → SQLite
上传接口 ──────┘          │
                          └→ CardrushClient → Cardrush 网站

日报工作流 → CardrushService → reporting renderer
```

约束：

- `hikari_bot/features/cardrush` 不导入 NoneBot、FastAPI 或 QQ 消息类型。
- `models.py` 只定义结构化价格数据。
- `client.py` 只负责网络请求和 Cardrush 页面解析。
- `repository.py` 只负责 SQLite schema、写入和查询。
- `service.py` 只负责应用用例、线程调度和客户端/仓储协调。
- `reporting` 是可选模块，不参与核心价格写入和查询。
- `plugins/monitors/cardrush.py` 只保留命令、QQ 输出转换和 scheduler 注册。
- `services/price.py` 在迁移期保留为兼容入口，转发到新模块。

## 数据模型

使用不可变 dataclass 表达 Cardrush 核心数据，避免跨层传递含义不明确的字典：

```python
@dataclass(frozen=True)
class PriceRecord:
    product_id: int
    name: str
    price: int
    rarity: str | None
    model_number: str | None
    updated_at: str | None


@dataclass(frozen=True)
class PriceSnapshot:
    product_id: int
    name: str
    price: int
    rarity: str | None
    model_number: str | None
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

外围适配层负责在 Pydantic 模型、dataclass、QQ 文本和 JSON 之间转换。

## 核心接口

`CardrushService` 对调用方提供统一异步接口：

```python
class CardrushService:
    async def search_prices(
        self,
        name: str,
        rarity: str | None = None,
        model_number: str | None = None,
        limit: int = 10,
    ) -> list[PriceSnapshot]: ...

    async def get_price_history(
        self,
        product_id: int,
    ) -> list[PricePoint]: ...

    async def get_daily_changes(
        self,
        date: str,
        exclude_prefixes: list[str] | None = None,
    ) -> list[PriceChange]: ...

    async def save_prices(
        self,
        records: list[PriceRecord],
    ) -> int: ...

    async def refresh_prices(self) -> int: ...
```

异步策略：

- Service 对外统一使用异步方法，适配 NoneBot、FastAPI 和未来网站。
- SQLite 继续使用标准库 `sqlite3`，由 Service 放入工作线程，避免阻塞事件循环。
- 网络抓取暂时保留现有代理、超时和页面解析行为。
- Repository 构造时接收数据库路径，生产环境使用原 `cardrush_prices.db`，测试使用临时数据库。

## 主要数据流

### 卡价查询

```text
QQ 输入
→ parsing 解析卡名、稀有度和卡盒编号
→ CardrushService.search_prices
→ PriceRepository 查询 SQLite
→ QQ 插件生成原有文本
```

### 定时刷新

```text
scheduler
→ CardrushService.refresh_prices
→ CardrushClient 抓取全部价格
→ PriceRepository.save_prices
→ 返回新增变化数量
```

### 价格上传

```text
→ Pydantic 校验
→ 转换为 PriceRecord
→ CardrushService.save_prices
→ PriceRepository 写入 SQLite
```

路由地址、请求头鉴权、请求体和响应体保持不变。

### 日报

```text
CardrushService.get_daily_changes
→ reporting 分类、排序和分页
→ DailyReportRenderer 生成图片
→ QQ 或 B 站适配层发送
```

## 报表与曲线

报表模块提供统一渲染入口：

```python
class DailyReportRenderer:
    async def render(
        self,
        changes: list[PriceChange],
        date: str,
    ) -> list[bytes]:
        """返回按页排列的图片字节。"""
```

手动“卡价图报”和 22:20 自动日报共享以下流程：

```text
价格变化
→ 下载卡图
→ 生成分页 HTML
→ Playwright 渲染
→ 返回图片列表
→ 清理临时文件
```

具体调整：

- `chart.py` 保留当前 QQ 价格曲线绘制，未来网站上线后可整体删除。
- `text.py` 保留现有 QQ 文字日报格式和分页。
- `html.py` 负责纯 HTML 构建、排序和分页。
- `daily_report.css` 承载原 Python 中的长 CSS 字符串。
- `renderer.py` 负责卡图下载、Playwright 生命周期和截图。
- `workflow.py` 负责查询变化并调用 renderer，不发送 QQ 消息。
- QQ 和 B 站发送行为仍位于外围适配层。

每次渲染使用独立的系统临时目录，包含图片、HTML 和截图。成功或失败都清理该目录，不再共用 `DATA_DIR/card_images` 和 `DATA_DIR/daily_report_html`，避免并发任务互相删除资源。

## 机器人适配层

`plugins/monitors/cardrush.py` 保留：

- 当前命令及别名注册。
- QQ 参数提取与用户提示。
- 结构化结果到 QQ 文本、图片的转换。
- 15 分钟价格检查任务。
- Asia/Tokyo 22:20 自动日报任务。
- 管理员命令和现有权限。

暂时保留：

- `卡价` / `卡价查询`
- `卡价曲线` / `历史卡价` / `卡价历史`
- `卡价日报`
- `卡价图报`
- `重置卡价数据库`

未来网站上线后，可以分别删除查询、曲线和手动日报 handler，而不修改 Service、Repository 或上传接口。如果不再需要自动日报，也可以删除整个 `reporting` 子模块。

## 错误处理

核心层定义：

```python
class CardrushError(Exception): ...
class CardrushClientError(CardrushError): ...
class CardrushRepositoryError(CardrushError): ...
class CardrushRenderError(CardrushError): ...
```

- Client 将网络错误、非成功状态和页面结构变化包装成 `CardrushClientError`。
- Repository 将 SQLite 失败包装成 `CardrushRepositoryError`，事务失败时回滚。
- Renderer 将卡图、HTML 和 Playwright 失败包装成 `CardrushRenderError`，并确保资源关闭和临时目录清理。
- 插件和路由把核心异常转换为现有用户提示或 HTTP 错误。
- 核心层不记录 QQ 文案，也不直接发送通知。

## 测试策略

新增：

```text
tests/cardrush/
  test_parsing.py
  test_client.py
  test_repository.py
  test_service.py
  test_text_report.py
  test_html_report.py
  test_report_workflow.py
  test_plugin_import.py
```

覆盖：

- 卡名、稀有度和卡盒参数解析。
- 日文稀有度与英文缩写映射。
- 固定 HTML fixture 中的 `__NEXT_DATA__` 解析。
- 首次保存、价格变化和停收写入 `price=0`。
- 当前价格搜索和历史记录排序。
- 每日新增、涨价、降价分类。
- 文字日报内容和分页。
- HTML 页数、排序和占位。
- 报表工作流的多页图片输出。
- 插件导入及原命令注册冒烟测试。

测试约束：

- SQLite 测试只使用临时数据库。
- 网络测试只使用固定 fixture，不访问真实 Cardrush。
- 报表工作流使用可替换截图后端，不要求单元测试启动真实浏览器。
- 最终单独执行一次真实 Playwright 渲染冒烟验证。
- 生产代码遵循测试先行的红—绿—重构循环。

## 迁移顺序

1. 为现有纯函数和数据库行为添加特征测试。
2. 提取 models 和 errors。
3. 提取 repository，保持原 schema。
4. 提取 client，保持原网络行为。
5. 增加异步 Service 和 `services/price.py` 兼容层。
6. 提取 parsing、chart、text 和 html。
7. 合并 renderer 和 report workflow。
8. 将 Cardrush 插件缩为适配层。
10. 执行完整测试、静态检查和真实渲染验证。

每一步均保持测试通过，不进行一次性整体重写。

## 验收标准

- 原有 Cardrush 命令均能注册。
- 命令名、别名、权限、回复格式和 scheduler 配置不变。
- 原 SQLite 数据无需迁移即可读取。
- 重构前后数据库 schema 一致。
- 手动、自动和 B 站报表共用同一渲染流程。
- `hikari_bot/features/cardrush` 不导入 NoneBot 或 FastAPI。
- `plugins/monitors/cardrush.py` 只承担接入职责。
- 测试、`compileall` 和静态检查通过。
- 工作树不产生遗留 HTML、截图或卡图缓存。

## 风险控制

- 使用 `services/price.py` 兼容层降低迁移期间的导入破坏。
- 先写特征测试，再移动现有逻辑。
- 不同时改变数据库 schema、外部数据源和用户可见行为。
- 真实 Cardrush 网络与 Playwright 验证放在单元测试之后单独执行，便于区分代码问题和环境问题。
