# FAQ、帮助功能与配置清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 FAQ/裁定查询和图片帮助功能，并把命令前缀与卡表网址收敛为代码常量。

**Architecture:** 使用一个纯标准库回归测试文件约束“被删除的能力必须不存在”和“固定产品常量必须保留”。配置清理、FAQ 删除、帮助删除分别完成一个红绿循环，最后同步 README 和生产配置示例。

**Tech Stack:** Python 3.10+、pytest、AST/文本契约测试、NoneBot 2 配置文件。

## Global Constraints

- `COMMAND_START` 固定为 `{""}`，带 `/` 前缀的命令不再触发。
- 卡表网站固定为 `https://ygo.xyk.one/deck`。
- FAQ/裁定查询、帮助命令和旧帮助资源必须完整删除，不保留功能开关或占位命令。
- 不调整其他卡片查询功能，不处理与本次范围无关的静态检查问题。
- 当前环境没有安装 NoneBot，自动化回归测试不得导入 NoneBot 插件。

---

### Task 1: 收紧配置契约

**Files:**
- Create: `tests/test_removed_features_contract.py`
- Modify: `bot.py`
- Modify: `hikari_bot/core/config.py`
- Modify: `hikari_bot/core/constants.py`
- Modify: `hikari_bot/plugins/ygomatch_query.py`

**Interfaces:**
- Consumes: `Settings` dataclass、`nonebot.init()`、比赛插件的卡表命令。
- Produces: `PUBLIC_DECK_URL: str` 常量；固定 `command_start={""}` 的启动配置。

- [ ] **Step 1: 写配置契约的失败测试**

```python
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _settings_fields() -> set[str]:
    tree = ast.parse(_source("hikari_bot/core/config.py"))
    settings_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    return {
        target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance((target := node.target), ast.Name)
    }


def test_removed_deployment_settings_are_not_declared():
    assert {
        "command_start",
        "faq_relay_group_id",
        "public_deck_url",
    }.isdisjoint(_settings_fields())


def test_command_prefix_and_public_deck_url_are_fixed_in_code():
    bot_source = "".join(_source("bot.py").split())
    constants_source = _source("hikari_bot/core/constants.py")
    match_source = _source("hikari_bot/plugins/ygomatch_query.py")

    assert 'command_start={""}' in bot_source
    assert 'PUBLIC_DECK_URL = "https://ygo.xyk.one/deck"' in constants_source
    assert "settings.public_deck_url" not in match_source
    assert "PUBLIC_DECK_URL" in match_source
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `python -m pytest tests/test_removed_features_contract.py -v`

Expected: 两个测试失败；失败原因分别是旧 Settings 字段仍存在，以及 `bot.py`/卡表网址仍读取 Settings。

- [ ] **Step 3: 写最小实现**

在 `bot.py` 中改为：

```python
nonebot.init(
    superusers=set(settings.superusers),
    command_start={""},
)
```

从 `Settings` 删除 `command_start`、`faq_relay_group_id`、`public_deck_url`，并删除不再使用的 `_optional_int()`。

在 `hikari_bot/core/constants.py` 添加：

```python
PUBLIC_DECK_URL = "https://ygo.xyk.one/deck"
```

比赛插件显式导入并使用该常量：

```python
from hikari_bot.core.constants import ADMIN, DECK_DIR, PUBLIC_DECK_URL

await deck_list.finish(f"请访问最新网页版 {PUBLIC_DECK_URL}")
```

- [ ] **Step 4: 运行配置契约测试并确认通过**

Run: `python -m pytest tests/test_removed_features_contract.py -v`

Expected: 两个测试 PASS。

- [ ] **Step 5: 提交配置清理**

```bash
git add tests/test_removed_features_contract.py bot.py hikari_bot/core/config.py hikari_bot/core/constants.py hikari_bot/plugins/ygomatch_query.py
git commit -m "refactor: fix command and deck URL configuration"
```

---

### Task 2: 删除 FAQ/裁定查询

**Files:**
- Modify: `tests/test_removed_features_contract.py`
- Modify: `hikari_bot/plugins/ygocard_query.py`
- Modify: `hikari_bot/services/ygocard.py`

**Interfaces:**
- Consumes: 卡片查询插件和 YGOCDB 服务源码。
- Produces: 不再暴露 FAQ URL、`get_qa_by_id()` 或裁定命令的卡片查询功能。

- [ ] **Step 1: 添加 FAQ 删除的失败测试**

```python
def test_faq_command_and_service_are_removed():
    plugin_source = _source("hikari_bot/plugins/ygocard_query.py")
    service_source = _source("hikari_bot/services/ygocard.py")
    service_tree = ast.parse(service_source)
    function_names = {
        node.name
        for node in ast.walk(service_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for removed_command in ("裁定查询", "游戏王裁定", '"裁定"'):
        assert removed_command not in plugin_source
    assert "get_qa_by_id" not in function_names
    assert 'FAQ = "https://ygocdb.com/faq/"' not in service_source
```

- [ ] **Step 2: 运行单测并确认按预期失败**

Run: `python -m pytest tests/test_removed_features_contract.py::test_faq_command_and_service_are_removed -v`

Expected: FAIL，指出裁定命令或 FAQ 服务仍存在。

- [ ] **Step 3: 写最小实现**

删除 `hikari_bot/plugins/ygocard_query.py` 中从 `ygo_card_faq = on_cmd(...)` 开始，到数据库维护注释前结束的整个处理器，并删除该文件不再使用的：

```python
from hikari_bot.core.config import settings
```

删除 `hikari_bot/services/ygocard.py` 中：

```python
from bs4.element import NavigableString
FAQ = "https://ygocdb.com/faq/"
async def get_qa_by_id(...):
    ...
```

同时把卡片插件模块说明中的 `FAQ 裁定查询` 删除。

- [ ] **Step 4: 运行 FAQ 删除测试并确认通过**

Run: `python -m pytest tests/test_removed_features_contract.py::test_faq_command_and_service_are_removed -v`

Expected: PASS。

- [ ] **Step 5: 提交 FAQ 删除**

```bash
git add tests/test_removed_features_contract.py hikari_bot/plugins/ygocard_query.py hikari_bot/services/ygocard.py
git commit -m "refactor: remove FAQ query feature"
```

---

### Task 3: 删除帮助命令和旧资源

**Files:**
- Modify: `tests/test_removed_features_contract.py`
- Modify: `hikari_bot/plugins/base.py`
- Delete: `hikari_bot/resources/help.png`
- Delete: `hikari_bot/resources/help.md`
- Delete: `.crossnote/config.js`
- Delete: `.crossnote/head.html`
- Delete: `.crossnote/parser.js`
- Delete: `.crossnote/style.less`

**Interfaces:**
- Consumes: 基础插件和旧帮助资源路径。
- Produces: 不再注册帮助命令、不再携带旧帮助生成资源的机器人。

- [ ] **Step 1: 添加帮助删除的失败测试**

```python
def test_help_command_and_resources_are_removed():
    base_source = _source("hikari_bot/plugins/base.py")
    assert 'on_cmd("帮助"' not in base_source
    assert 'aliases={"help"}' not in base_source
    assert "help.png" not in base_source

    for relative in (
        "hikari_bot/resources/help.png",
        "hikari_bot/resources/help.md",
        ".crossnote",
    ):
        assert not (ROOT / relative).exists()
```

- [ ] **Step 2: 运行单测并确认按预期失败**

Run: `python -m pytest tests/test_removed_features_contract.py::test_help_command_and_resources_are_removed -v`

Expected: FAIL，指出帮助命令或旧资源仍存在。

- [ ] **Step 3: 写最小实现**

从 `hikari_bot/plugins/base.py` 删除帮助命令代码块，并删除仅由该代码块使用的导入：

```python
import base64
MessageSegment
from hikari_bot.core.constants import RESOURCES_DIR
```

保留 `Message`，因为白名单和广播命令的 `CommandArg()` 类型注解仍然使用它。

删除两个帮助资源文件和整个 `.crossnote` 目录。

- [ ] **Step 4: 运行帮助删除测试并确认通过**

Run: `python -m pytest tests/test_removed_features_contract.py::test_help_command_and_resources_are_removed -v`

Expected: PASS。

- [ ] **Step 5: 提交帮助清理**

```bash
git add tests/test_removed_features_contract.py hikari_bot/plugins/base.py hikari_bot/resources .crossnote
git commit -m "refactor: remove legacy help feature"
```

---

### Task 4: 同步文档和生产配置示例

**Files:**
- Modify: `tests/test_removed_features_contract.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: 已清理的配置与功能集合。
- Produces: 与实际运行能力一致的生产配置文档。

- [ ] **Step 1: 添加文档残留的失败测试**

```python
def test_docs_do_not_advertise_removed_features_or_settings():
    env_example = _source(".env.example")
    readme = _source("README.md")

    for removed_setting in (
        "COMMAND_START",
        "FAQ_RELAY_GROUP_ID",
        "PUBLIC_DECK_URL",
    ):
        assert removed_setting not in env_example
        assert removed_setting not in readme

    assert "查裁定" not in readme
    assert "- 帮助" not in readme
```

- [ ] **Step 2: 运行单测并确认按预期失败**

Run: `python -m pytest tests/test_removed_features_contract.py::test_docs_do_not_advertise_removed_features_or_settings -v`

Expected: FAIL，指出 README 或 `.env.example` 仍包含旧配置/功能。

- [ ] **Step 3: 写最小实现**

从 `.env.example` 删除：

```dotenv
COMMAND_START=["", "/"]
FAQ_RELAY_GROUP_ID=123456789
PUBLIC_DECK_URL=https://example.com/deck
```

从 README 的生产配置段删除相同配置，从功能列表删除“查裁定”和“帮助”。

- [ ] **Step 4: 运行全部契约测试**

Run: `python -m pytest tests/test_removed_features_contract.py -v`

Expected: 全部测试 PASS。

- [ ] **Step 5: 提交文档同步**

```bash
git add tests/test_removed_features_contract.py .env.example README.md
git commit -m "docs: remove FAQ and help configuration"
```

---

### Task 5: 全仓库验证

**Files:**
- Verify only: all changed files.

**Interfaces:**
- Consumes: Tasks 1-4 的完整结果。
- Produces: 可交付的验证记录和服务器配置清单。

- [ ] **Step 1: 运行完整契约测试**

Run: `python -m pytest tests/test_removed_features_contract.py -v`

Expected: 全部 PASS，输出无警告。

- [ ] **Step 2: 运行语法编译**

Run: `python -m compileall -q bot.py hikari_bot scripts`

Expected: exit code 0。

- [ ] **Step 3: 扫描残留**

Run:

```bash
rg -n "FAQ_RELAY_GROUP_ID|PUBLIC_DECK_URL|COMMAND_START|get_qa_by_id|ygocdb.com/faq|裁定查询|游戏王裁定|help.png|on_cmd\\(\"帮助\"" README.md .env.example bot.py hikari_bot scripts
```

Expected: 只允许 `hikari_bot/core/constants.py` 中固定的 `PUBLIC_DECK_URL`；其他模式无匹配。

- [ ] **Step 4: 检查补丁格式和工作区**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exit code 0；状态只包含本轮及上一阶段已知配置改动。

- [ ] **Step 5: 汇总服务器配置**

最终回复将 `.env.prod` 分为：

- 必需：`ONEBOT_ACCESS_TOKEN`、`SUPERUSERS`、`UPTIME_TOKEN`、`API_TIMEOUT`、`PARSER_DISABLED_PLATFORMS`。
- 按功能启用：`JIHUANSHE_TOKEN`、`CARDRUSH_PROXY_URL`、`PUBLIC_GROUP_ID`、`BLOG_DEPLOY_SCRIPT`、`BLOG_UPDATE_SCRIPT`、`JM_DATA_DIR`、`JM_PDF_PASSWORD`。

明确说明不再需要：`COMMAND_START`、`FAQ_RELAY_GROUP_ID`、`PUBLIC_DECK_URL`。
