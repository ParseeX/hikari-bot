# 统一持久化基础设施设计

## 目标

将功能开关、群白名单、MyCard 绑定和订阅从 JSON 运行时状态迁移到可靠的
SQLite 仓储，并通过一次性自动迁移完整保留既有数据。赛事状态及赛事功能保持
完全不变。

## 范围

- 增加小型共享持久化基础设施，负责 SQLite 连接配置、事务、数据库版本记录和
  启动初始化。
- 为功能开关、群白名单、MyCard 用户绑定和 MyCard 订阅增加 SQLite 仓储。
- 将低频机器人状态存入 `data/hikari.db`。
- 首次启动时，在一个事务中迁移 `feature_flags.json`、`whitelist.json`、
  `mycard_user.json` 和 `subscribe.json`。
- 成功导入后，将每个旧文件改名为相同文件名加 `.bak` 后缀，且绝不覆盖已有
  备份。
- 在合理范围内保留现有公开服务函数，令插件和监控模块只需进行小范围改动。
- 为数据库初始化、迁移、幂等性、回滚和仓储行为补充测试。

## 非目标

- 不迁移或修改 `match_state.json`、赛事命令和赛事卡组附件。
- 不将 Cardrush 价格历史合并进 `hikari.db`。
- 不将第三方卡表 `card.cdb`、图片缓存、PDF 输出或 YDK 文件迁入 SQLite。
- 不引入通用 ORM。

## 存储边界

```text
data/
  hikari.db             低频机器人状态：开关、白名单、MyCard 状态
  cardrush_prices.db    Cardrush 市场价格历史，继续独立保存
  cache/                可重建的第三方卡表和图片缓存
  artifacts/            卡组文件和生成的二进制输出
```

共享持久化基础设施不要求所有领域共用一个物理数据库。Cardrush 继续保留独立
仓储和数据库，因为价格历史增长较快，且应能独立重置或备份，不影响用户状态。

## 组件

### 数据库基础设施

`hikari_bot/persistence/database.py` 提供受限的连接和事务接口。每个连接均启用
外键、WAL 模式和等待超时，避免命令、Web 路由和监控任务发生短暂并发写入时立即
报出 `database is locked`。

### 迁移执行器

`hikari_bot/persistence/migrations.py` 创建 `schema_migrations` 表，并在启动时按
顺序执行尚未应用的迁移。它还会恰好执行一次旧 JSON 导入：只有状态表为空且不
存在旧数据导入标记时才会导入。

### 状态仓储

- `FeatureFlagsRepository`：读取和设置具名布尔开关。
- `WhitelistRepository`：添加、列出、判断和清空群号与用户号。
- `MyCardRepository`：绑定 QQ 与 MyCard 用户名，管理唯一的
  `(username, target_type, target_id)` 订阅记录。

仓储独占 SQL；插件和服务不得直接打开 JSON 文件或 SQLite 连接。

## 数据表

```sql
CREATE TABLE feature_flags (
  name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
);

CREATE TABLE whitelist_groups (
  group_id TEXT PRIMARY KEY
);

CREATE TABLE whitelist_users (
  user_id TEXT PRIMARY KEY
);

CREATE TABLE mycard_bindings (
  qq_id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE mycard_subscriptions (
  username TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('group', 'private')),
  target_id TEXT NOT NULL,
  PRIMARY KEY (username, target_type, target_id)
);
```

默认值保持与现有行为一致：缺少 `mycard_notify` 或 `mensa_monitor` 开关时，均视为
已启用。

## 旧数据迁移流程

```text
应用启动
  -> 创建或升级 hikari.db 数据表
  -> 若旧数据导入尚未执行且状态表为空：
       校验全部存在的 JSON 文件
       将已校验文件原子改名为 *.bak
       在一个事务中导入全部有效数据并写入导入标记
  -> 启动插件、监控任务和 Web 路由
```

若 JSON 解析、字段校验、插入或备份改名失败，数据库事务必须回滚。事务失败时应将
已改名的 `.bak` 文件还原为原始文件名；若进程恰好在二者之间异常退出，下一次启动
必须把 `.bak` 识别为待导入的旧数据来源并恢复迁移。不得将局部导入视为成功。

若数据库已有状态，则以数据库为准：不导入旧 JSON，也不允许旧 JSON 覆盖数据库。

## 运行时数据流

```text
插件 / 监控 / Web 路由
  -> 现有服务层函数
  -> 具体领域仓储
  -> 共享数据库基础设施
  -> hikari.db
```

初次改造可保留同步仓储 API，并在异步处理器中转入工作线程，降低插件改动范围。
共享基础设施必须保持事务足够短，禁止在事务中执行网络请求、渲染或文件下载。

## 异常处理

- 仓储方法抛出小型持久化专用异常，包含操作上下文，但不向用户泄露原始 SQL。
- 既有插件维持原有用户提示，并记录详细错误日志。
- 缺少旧 JSON 文件属于合法情况，按空状态处理。
- 旧 JSON 格式非法时终止导入，不得静默丢弃记录。
- 通过主键忽略重复订阅，结果与当前幂等订阅行为一致。

## 验证方式

- 单元测试使用临时数据库覆盖每个仓储。
- 迁移测试覆盖成功导入、文件缺失、JSON 非法、重复启动、已有数据库优先和回滚。
- 兼容性测试验证 MyCard 监控和白名单命令维持既有数据形状和结果。
- 既有 Cardrush 仓储继续独立测试，不与 `hikari.db` 耦合。

## 验收标准

1. 首次启动无需手工操作即可导入既有开关、白名单、MyCard 绑定和订阅数据。
2. 成功导入后保留可恢复的 `.bak` 文件，后续启动绝不重复导入。
3. 导入失败时数据库状态和原始旧文件均保持不变。
4. 运行时对四类已迁移状态的读写不再依赖 JSON 文件。
5. 赛事状态继续以 JSON 保存，且功能不受影响。
6. Cardrush 继续使用 `data/cardrush_prices.db`，并沿用现有仓储边界。
7. 此次新增或修改的代码注释、设计文档和实施计划均使用中文。
