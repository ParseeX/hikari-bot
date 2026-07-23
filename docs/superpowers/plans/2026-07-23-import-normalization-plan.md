# 导入规范化实施计划

> **供自动化执行者使用：** 实施时必须使用 `superpowers:executing-plans`，按任务逐项执行和验证。

**目标：** 在不改变业务行为和插件发现机制的前提下，消除通配符导入，补齐真实依赖，并清理与本次修改相关的无用导入。

**架构：** 少量稳定函数采用显式函数导入；大量使用同一业务服务时，导入带业务别名的模块命名空间。NoneBot 继续通过 `plugin_dirs` 自动发现插件，`monitors/__init__.py` 保持不变。

**技术栈：** Python、NoneBot、pyflakes、compileall。

## 全局约束

- 所有说明和文档使用中文。
- 不修改命令、定时任务、路由和生命周期行为。
- 不修改插件注册机制。
- 不新增业务功能。
- 不使用 `from ... import *`。
- 生产代码修改前必须先确认当前 `pyflakes` 基线能够暴露问题。

---

### 任务一：规范基础插件和白名单模块的导入

**文件：**

- 修改：`hikari_bot/plugins/base.py`
- 修改：`hikari_bot/core/whitelist.py`

**接口：**

- 使用：`add_group_to_whitelist()`、`get_whitelist()`、`is_allowed_group()`、`message_superusers()`、`save_whitelist()`
- 不改变以上函数的签名和调用方式。

- [ ] **步骤 1：确认失败基线**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/base.py hikari_bot/core/whitelist.py
```

预期：报告 `base.py` 使用通配符导入，并因通配符无法确定白名单函数来源；同时报告若干无用导入。

- [ ] **步骤 2：改为显式函数导入**

将 `base.py` 中的白名单导入改为：

```python
from hikari_bot.core.whitelist import (
    add_group_to_whitelist,
    get_whitelist,
    is_allowed_group,
    message_superusers,
    save_whitelist,
)
```

删除 `base.py` 中未使用的 `re`、`on_notice`、`Event`、`PrivateMessageEvent`，删除 `whitelist.py` 中未使用的 `get_driver` 和 `Bot`。

- [ ] **步骤 3：验证任务一**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/base.py hikari_bot/core/whitelist.py
python -m compileall -q hikari_bot/plugins/base.py hikari_bot/core/whitelist.py
```

预期：两个命令退出码均为 0。

### 任务二：通过服务命名空间访问 MyCard

**文件：**

- 修改：`hikari_bot/plugins/mycard_query.py`

**接口：**

- 使用：`hikari_bot.services.mycard` 现有公开函数。
- 产生：模块别名 `mycard_service`。
- 不修改 MyCard 服务本身。

- [ ] **步骤 1：确认失败基线**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/mycard_query.py
```

预期：报告 `from hikari_bot.services.mycard import *`，并将服务函数标记为来源不确定。

- [ ] **步骤 2：导入服务模块**

将通配符导入替换为：

```python
from hikari_bot.services import mycard as mycard_service
```

为以下调用增加 `mycard_service.` 前缀：

```python
get_mycard_user
mycard_get_records
fetch_player_history_rank
mycard_get_player_rank
add_mycard_user
fetch_player_history
subscribe
unsubscribe
is_first_win
```

不处理与导入无关的业务逻辑。

- [ ] **步骤 3：验证任务二**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/mycard_query.py
python -m compileall -q hikari_bot/plugins/mycard_query.py
```

预期：不再出现通配符导入或服务函数来源不确定的报告；若仍有既存的非导入类提示，单独记录，不借机修改业务逻辑。

### 任务三：通过服务命名空间访问卡片服务

**文件：**

- 修改：`hikari_bot/plugins/ygocard_query.py`

**接口：**

- 使用：`hikari_bot.services.ygocard` 现有公开函数。
- 产生：模块别名 `card_service`。

- [ ] **步骤 1：确认失败基线**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/ygocard_query.py
```

预期：报告通配符导入、`FinishedException` 和 `io` 来源不明确或未定义。

- [ ] **步骤 2：建立明确依赖**

导入标准库模块和异常：

```python
import io

from nonebot.exception import FinishedException
```

删除未使用的：

```python
from io import BytesIO
```

将卡片服务通配符导入替换为：

```python
from hikari_bot.services import ygocard as card_service
```

为以下调用增加 `card_service.` 前缀：

```python
get_ygopic
random_card
is_card_id
get_card_info
get_image_by_id
update_cdb
metaltronus_calc
```

- [ ] **步骤 3：验证任务三**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/ygocard_query.py
python -m compileall -q hikari_bot/plugins/ygocard_query.py
```

预期：不再出现通配符导入或未定义名称。

### 任务四：通过服务命名空间访问比赛和卡组服务

**文件：**

- 修改：`hikari_bot/plugins/ygomatch_query.py`

**接口：**

- 使用：`hikari_bot.services.ygomatch`
- 使用：`hikari_bot.services.ygodeck`
- 产生：模块别名 `match_service`、`deck_service`

- [ ] **步骤 1：确认失败基线**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/ygomatch_query.py
```

预期：报告比赛服务通配符导入，并报告 `generate_deck_image` 未明确导入。

- [ ] **步骤 2：改为服务模块导入**

使用：

```python
from hikari_bot.services import ygodeck as deck_service
from hikari_bot.services import ygomatch as match_service
```

删除原有 `ygodeck` 函数列表导入和 `ygomatch` 通配符导入。

为卡组调用增加 `deck_service.` 前缀：

```python
get_deck_text_from_url
is_deck_code
is_deck_url
save_deck_text_as_ydk
generate_deck_image
```

为比赛调用增加 `match_service.` 前缀：

```python
get_match_state
get_contestants
search_by_keyword
get_match_detail
get_tournament_info
save_match_state
match_quit
start_tournament
reset_match_state
match_check_in
get_pairing
```

- [ ] **步骤 3：验证任务四**

运行：

```powershell
python -m pyflakes hikari_bot/plugins/ygomatch_query.py
python -m compileall -q hikari_bot/plugins/ygomatch_query.py
```

预期：不再出现通配符导入、比赛服务来源不确定或 `generate_deck_image` 未定义。

### 任务五：补齐底层服务和 Web 路由的直接导入

**文件：**

- 修改：`hikari_bot/services/ygodeck.py`
- 修改：`hikari_bot/plugins/web/routes/sms.py`
- 修改：`hikari_bot/plugins/web/routes/deck.py`
- 修改：`hikari_bot/services/ygocard.py`
- 修改：`hikari_bot/plugins/monitors/cardrush.py`

**接口：**

- `ygodeck.py` 直接使用 `PDF_DIR`、`log_message`、`get_ygopic`。
- `sms.py` 直接使用 `log_message`。

- [ ] **步骤 1：确认失败基线**

运行：

```powershell
python -m pyflakes hikari_bot/services/ygodeck.py hikari_bot/plugins/web/routes/sms.py
```

预期：报告 `get_ygopic`、`BytesIO`、`log_message`、`PDF_DIR` 和短信日志函数未定义。

- [ ] **步骤 2：补齐 `ygodeck.py` 依赖**

改为：

```python
from hikari_bot.core.constants import DATA_DIR, PDF_DIR, RESOURCES_DIR
from hikari_bot.core.logger import log_message
from hikari_bot.services.ygocard import get_card_info_by_id, get_ygopic
```

将裸 `BytesIO(...)` 改为 `io.BytesIO(...)`，避免同时存在两种导入方式。

- [ ] **步骤 3：补齐短信日志并清理确定无用导入**

在 `sms.py` 中加入：

```python
from hikari_bot.core.logger import log_message
```

同时删除静态检查确认无用的：

- `hikari_bot/plugins/web/routes/deck.py` 中的 `os`
- `hikari_bot/services/ygocard.py` 中的 `BytesIO` 和 `Image`
- `hikari_bot/plugins/monitors/cardrush.py` 中的 `message_superusers`

- [ ] **步骤 4：验证任务五**

运行：

```powershell
python -m pyflakes hikari_bot/services/ygodeck.py hikari_bot/plugins/web/routes/sms.py hikari_bot/plugins/web/routes/deck.py hikari_bot/services/ygocard.py hikari_bot/plugins/monitors/cardrush.py
python -m compileall -q hikari_bot/services/ygodeck.py hikari_bot/plugins/web/routes/sms.py hikari_bot/plugins/web/routes/deck.py hikari_bot/services/ygocard.py hikari_bot/plugins/monitors/cardrush.py
```

预期：不再出现未定义名称和上述无用导入。

### 任务六：全仓库验证

**文件：**

- 检查：`bot.py`
- 检查：`hikari_bot/**/*.py`
- 检查：`scripts/*.py`

- [ ] **步骤 1：确认通配符已经清零**

运行：

```powershell
rg -n '^from .* import \*' bot.py hikari_bot scripts
```

预期：无输出，`rg` 退出码为 1，表示没有匹配项。

- [ ] **步骤 2：执行静态检查**

运行：

```powershell
python -m pyflakes bot.py hikari_bot scripts
```

预期：不再包含未定义名称、通配符导入或与本次涉及文件相关的无用导入。既存且与导入无关的提示必须如实报告。

- [ ] **步骤 3：执行语法编译和测试**

运行：

```powershell
python -m compileall -q bot.py hikari_bot scripts
python -m pytest -q
```

预期：`compileall` 退出码为 0；若仓库仍无测试，`pytest` 会报告未收集到测试并返回退出码 5，需要在结果中明确说明，不能表述为测试通过。

- [ ] **步骤 4：检查差异范围**

运行：

```powershell
git diff --check
git status --short
git diff --stat
```

预期：没有空白错误；差异仅包含实施计划和本次导入规范化涉及的文件。
