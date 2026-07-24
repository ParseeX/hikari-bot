# 统一持久化基础设施实施计划

> **供代理执行者使用：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务执行并在任务间审查。任务均使用复选框追踪。

**目标：** 将功能开关、白名单和 MyCard 绑定/订阅无损迁移到 SQLite，并保留赛事 JSON 与独立的 Cardrush 价格数据库。

**架构：** 新增 `persistence` 包，集中管理 `data/hikari.db` 的连接、事务、模式版本和旧 JSON 迁移。现有 `core` 与 `services/mycard.py` 保留对插件兼容的函数形状，但改为委托给 SQLite 仓储；Cardrush 继续使用现有 `data/cardrush_prices.db`。

**技术栈：** Python 3.10+、标准库 `sqlite3`、`pytest`、NoneBot。

## 全局约束

- 所有新增或修改的代码注释、设计文档、实施计划和用户提示均使用中文。
- 仅迁移 `feature_flags.json`、`whitelist.json`、`mycard_user.json` 和 `subscribe.json`。
- `match_state.json`、赛事模块、卡组附件、`card.cdb`、图片缓存和 Cardrush 数据库均不得修改。
- `hikari.db` 是白名单、开关和 MyCard 状态的唯一运行时数据源；旧 JSON 仅用于首次迁移与备份。
- 运行时不得在事务内执行网络请求、Playwright 渲染或文件下载。

---

## 文件结构

```text
hikari_bot/persistence/
  __init__.py              持久化入口和默认状态库单例
  database.py              连接配置、事务和持久化异常
  repositories.py          开关、白名单、MyCard 的 SQL 仓储
  migrations.py            数据表版本和旧 JSON 一次性迁移
tests/persistence/
  test_database.py         连接与事务测试
  test_repositories.py     三类仓储的行为测试
  test_migrations.py       JSON 迁移、恢复和幂等测试
```

### Task 1：建立 SQLite 基础设施

**文件：**
- 创建：`hikari_bot/persistence/__init__.py`
- 创建：`hikari_bot/persistence/database.py`
- 创建：`tests/persistence/test_database.py`

**接口：**
- 产生：`PersistenceError`、`StateDatabase(path: Path)`、`StateDatabase.connect()`、`StateDatabase.transaction()`。
- 供给：后续仓储与迁移任务通过 `StateDatabase` 获取短生命周期连接。

- [ ] **步骤 1：编写失败测试**

```python
def test_connect_creates_parent_and_configures_sqlite(tmp_path):
    database = StateDatabase(tmp_path / "nested" / "hikari.db")
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_transaction_rolls_back_on_error(tmp_path):
    database = StateDatabase(tmp_path / "hikari.db")
    with database.connect() as connection:
        connection.execute("CREATE TABLE values_table (value TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute("INSERT INTO values_table VALUES ('kept-out')")
            raise RuntimeError("测试回滚")
    with database.connect() as connection:
        assert connection.execute("SELECT value FROM values_table").fetchall() == []
```

- [ ] **步骤 2：确认测试失败**

运行：`pytest tests/persistence/test_database.py -v`

预期：失败，提示无法导入 `hikari_bot.persistence.database`。

- [ ] **步骤 3：实现最小基础设施**

```python
class StateDatabase:
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
```

将 `sqlite3.Error` 包装为带中文操作上下文的 `PersistenceError`；注释说明每个
SQLite pragma 的作用。

- [ ] **步骤 4：确认测试通过**

运行：`pytest tests/persistence/test_database.py -v`

预期：2 项通过。

- [ ] **步骤 5：提交**

```powershell
git add hikari_bot/persistence tests/persistence/test_database.py
git commit -m "feat: add sqlite persistence foundation"
```

### Task 2：实现状态仓储及其完整数据表

**文件：**
- 创建：`hikari_bot/persistence/repositories.py`
- 创建：`tests/persistence/test_repositories.py`
- 修改：`hikari_bot/persistence/__init__.py`

**接口：**
- 消费：`StateDatabase`。
- 产生：`StateRepository`，提供 `get_flag`、`set_flag`、`replace_whitelist`、
  `add_group`、`is_group_allowed`、`get_bindings`、`replace_bindings`、
  `subscribe`、`unsubscribe`、`unsubscribe_all` 和 `get_subscriptions`。

- [ ] **步骤 1：编写失败测试**

```python
def test_flags_default_to_enabled_and_persist_false(repository):
    assert repository.get_flag("mycard_notify", default=True) is True
    repository.set_flag("mycard_notify", False)
    assert repository.get_flag("mycard_notify", default=True) is False


def test_whitelist_preserves_groups_and_users(repository):
    repository.replace_whitelist(groups=["100"], users=["200"])
    assert repository.get_whitelist() == {"groups": ["100"], "users": ["200"]}
    assert repository.add_group("100") is False
    assert repository.add_group("101") is True


def test_mycard_subscription_is_unique_and_can_remove_target(repository):
    repository.set_binding("1", "alice")
    assert repository.get_bindings() == {"1": "alice"}
    assert repository.subscribe("group", "100", "alice") is True
    assert repository.subscribe("group", "100", "alice") is False
    assert repository.unsubscribe_all("group", "100") is True
    assert repository.get_subscriptions() == {}
```

- [ ] **步骤 2：确认测试失败**

运行：`pytest tests/persistence/test_repositories.py -v`

预期：失败，提示 `StateRepository` 未定义。

- [ ] **步骤 3：实现模式和仓储**

在仓储初始化方法中创建以下表及唯一约束：

```sql
feature_flags(name TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)));
whitelist_groups(group_id TEXT PRIMARY KEY);
whitelist_users(user_id TEXT PRIMARY KEY);
mycard_bindings(qq_id TEXT PRIMARY KEY, username TEXT NOT NULL, updated_at TEXT NOT NULL);
mycard_subscriptions(
  username TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('group', 'private')),
  target_id TEXT NOT NULL,
  PRIMARY KEY (username, target_type, target_id)
);
```

`get_whitelist()` 必须始终返回 `{"groups": [...], "users": [...]}`；
`get_subscriptions()` 必须返回现有监控模块可用的
`dict[str, list[list[str]]]`。所有列表按字符串排序，确保测试和日志稳定。

- [ ] **步骤 4：确认测试通过**

运行：`pytest tests/persistence/test_repositories.py -v`

预期：3 项通过。

- [ ] **步骤 5：提交**

```powershell
git add hikari_bot/persistence tests/persistence/test_repositories.py
git commit -m "feat: add state repositories"
```

### Task 3：实现模式版本和旧 JSON 的可恢复迁移

**文件：**
- 创建：`hikari_bot/persistence/migrations.py`
- 创建：`tests/persistence/test_migrations.py`
- 修改：`hikari_bot/persistence/__init__.py`

**接口：**
- 消费：`StateDatabase`、`StateRepository`。
- 产生：`initialize_state_store(database_path: Path, legacy_dir: Path) -> StateRepository`。

- [ ] **步骤 1：编写失败测试**

```python
def test_imports_json_once_and_keeps_backups(tmp_path):
    write_json(tmp_path / "whitelist.json", {"groups": ["10"], "users": ["20"]})
    write_json(tmp_path / "feature_flags.json", {"mensa_monitor": False})
    write_json(tmp_path / "mycard_user.json", {"1": "alice"})
    write_json(tmp_path / "subscribe.json", {"alice": [["group", "10"]]})

    store = initialize_state_store(tmp_path / "hikari.db", tmp_path)
    assert store.get_whitelist()["groups"] == ["10"]
    assert store.get_flag("mensa_monitor", True) is False
    assert store.get_bindings() == {"1": "alice"}
    assert store.get_subscriptions() == {"alice": [["group", "10"]]}
    assert (tmp_path / "whitelist.json.bak").is_file()

    second = initialize_state_store(tmp_path / "hikari.db", tmp_path)
    assert second.get_bindings() == {"1": "alice"}


def test_invalid_json_does_not_create_partial_state_or_backup(tmp_path):
    (tmp_path / "subscribe.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(PersistenceError):
        initialize_state_store(tmp_path / "hikari.db", tmp_path)
    assert not (tmp_path / "subscribe.json.bak").exists()
```

- [ ] **步骤 2：确认测试失败**

运行：`pytest tests/persistence/test_migrations.py -v`

预期：失败，提示 `initialize_state_store` 未定义。

- [ ] **步骤 3：实现迁移和恢复逻辑**

实现 `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)`；使用版本
`001_initial_state_schema` 和 `002_legacy_json_import`。迁移按以下顺序执行：

```python
sources = {
    "feature_flags": legacy_dir / "feature_flags.json",
    "whitelist": legacy_dir / "whitelist.json",
    "bindings": legacy_dir / "mycard_user.json",
    "subscriptions": legacy_dir / "subscribe.json",
}
# 先读取并校验所有存在的 JSON；缺少文件视作空数据。
# 每个原文件以 Path.replace() 改名为 *.bak；若失败则还原已经改名的文件。
# 在一个 StateDatabase.transaction() 中写入四类数据和 002 标记。
# 若事务失败，尝试把 *.bak 改回原名；下次启动也将 *.bak 视作待导入来源。
```

数据库已经有 `002_legacy_json_import` 标记时，不得重复导入；若标记存在但原 JSON
尚在，则只尝试补做备份改名，绝不覆盖数据库。JSON 字段类型不符、订阅目标类型不在
`group/private`、或列表项不是两个字符串时均抛出 `PersistenceError`。

- [ ] **步骤 4：确认测试通过**

运行：`pytest tests/persistence/test_migrations.py -v`

预期：迁移成功、幂等、非法 JSON 和中断恢复相关测试全部通过。

- [ ] **步骤 5：提交**

```powershell
git add hikari_bot/persistence tests/persistence/test_migrations.py
git commit -m "feat: migrate legacy state json to sqlite"
```

### Task 4：接入现有功能并保持外部行为兼容

**文件：**
- 修改：`bot.py`
- 修改：`hikari_bot/core/feature_flags.py`
- 修改：`hikari_bot/core/whitelist.py`
- 修改：`hikari_bot/services/mycard.py`
- 创建：`tests/persistence/test_legacy_api_compatibility.py`

**接口：**
- 消费：`initialize_state_store()` 和 `StateRepository`。
- 产生：现有 `get_*`、`set_*`、`subscribe`、`unsubscribe`、`unsubscribe_all` API 的
  SQLite 实现，供既有插件和监控模块无修改调用。

- [ ] **步骤 1：编写失败兼容性测试**

```python
def test_whitelist_compatibility_api_uses_sqlite(monkeypatch, state_store):
    monkeypatch.setattr(whitelist, "get_state_store", lambda: state_store)
    assert asyncio.run(whitelist.add_group_to_whitelist("100")) is True
    assert asyncio.run(whitelist.is_allowed_group("100")) is True
    assert asyncio.run(whitelist.get_whitelist()) == {"groups": ["100"], "users": []}


def test_mycard_compatibility_api_returns_monitor_shape(monkeypatch, state_store):
    monkeypatch.setattr(mycard, "get_state_store", lambda: state_store)
    mycard.add_mycard_user("1", "alice")
    mycard.subscribe("private", "1", "alice")
    assert mycard.get_mycard_user() == {"1": "alice"}
    assert mycard.get_subscribe_list() == {"alice": [["private", "1"]]}
```

- [ ] **步骤 2：确认测试失败**

运行：`pytest tests/persistence/test_legacy_api_compatibility.py -v`

预期：失败，因为现有模块仍读取 JSON。

- [ ] **步骤 3：改造启动和兼容包装函数**

在 `bot.py` 中、`nonebot.load_from_toml()` 之前调用：

```python
from hikari_bot.persistence import initialize_default_state_store

new_log_file()
initialize_default_state_store()
```

删除 `feature_flags.py`、`whitelist.py` 和 `services/mycard.py` 中 JSON 文件路径、
缓存和读写辅助函数；保留原公开函数名与返回类型。异步开关和白名单函数使用
`await asyncio.to_thread(...)` 调用仓储；MyCard 的既有同步 API 直接调用短事务仓储。
新增或修改的模块注释、函数文档和行内注释必须使用中文。

- [ ] **步骤 4：确认兼容测试和全量测试通过**

运行：

```powershell
pytest tests/persistence -v
pytest -q
python -m pyflakes bot.py hikari_bot scripts
python -m compileall -q bot.py hikari_bot scripts
```

预期：所有测试通过，静态检查不新增未定义名称，语法编译成功。

- [ ] **步骤 5：提交**

```powershell
git add bot.py hikari_bot/core/feature_flags.py hikari_bot/core/whitelist.py hikari_bot/services/mycard.py tests/persistence
git commit -m "feat: migrate bot state to sqlite"
```

### Task 5：为 Cardrush 复用基础数据库配置但保持物理隔离

**文件：**
- 修改：`hikari_bot/features/cardrush/repository.py`
- 修改：`tests/cardrush/test_repository.py`

**接口：**
- 消费：`StateDatabase` 的通用 SQLite 配置能力，或提炼出的无状态连接配置函数。
- 产生：维持 `PriceRepository` 全部既有公开方法和 `data/cardrush_prices.db` 路径不变。

- [ ] **步骤 1：编写失败回归测试**

```python
def test_initialize_enables_wal_without_changing_price_schema(tmp_path):
    db_path = tmp_path / "cardrush_prices.db"
    repository = PriceRepository(db_path)
    repository.initialize()
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'card_price_history'"
        ).fetchone()[0] == "card_price_history"
```

- [ ] **步骤 2：确认测试失败**

运行：`pytest tests/cardrush/test_repository.py::test_initialize_enables_wal_without_changing_price_schema -v`

预期：失败，因为 Cardrush 尚未使用共享连接配置。

- [ ] **步骤 3：提炼可复用的 SQLite 配置函数**

在 `database.py` 增加无状态 `configure_sqlite_connection(connection)`，设置外键、
WAL 和 5 秒等待超时；`StateDatabase.connect()` 与 `PriceRepository` 均调用它。
不得改变 Cardrush 的文件路径、表名、价格写入逻辑或重置行为。

- [ ] **步骤 4：确认 Cardrush 回归通过**

运行：`pytest tests/cardrush/test_repository.py -v`

预期：既有 Cardrush 仓储测试和新增 WAL 测试全部通过。

- [ ] **步骤 5：提交**

```powershell
git add hikari_bot/persistence/database.py hikari_bot/features/cardrush/repository.py tests/cardrush/test_repository.py
git commit -m "refactor: share sqlite connection configuration"
```

## 计划自检

- 规格中的自动迁移、备份、幂等、回滚、赛事排除、Cardrush 物理隔离和中文要求，均有对应任务。
- 未使用占位任务；每个代码任务都先给出失败测试、明确命令和通过条件。
- `StateDatabase`、`StateRepository`、`initialize_state_store` 的名称和签名在后续任务中保持一致。
