# 导入规范化设计

## 目标

在不改变运行行为、也不替换 NoneBot 目录自动发现插件机制的前提下，规范当前代码库的导入方式。

## 改动范围

- 将剩余的通配符导入全部替换为明确依赖。
- 一个模块只使用少量稳定函数时，直接显式导入这些函数。
- 一个调用方大量使用同一服务模块时，通过具有业务含义的模块命名空间访问。
- 为当前已引用但未在模块中定义的名称补充直接导入。
- 删除经过静态检查确认未使用的导入。
- 保持 Matcher、定时任务、生命周期钩子和 Web 路由的注册行为不变。

## 导入规则

导入按以下顺序分组：

1. Python 标准库
2. 第三方依赖
3. 项目内部模块

不同分组之间保留一个空行。

调用方只使用少量名称时，直接显式导入：

```python
from hikari_bot.core.whitelist import (
    add_group_to_whitelist,
    get_whitelist,
    is_allowed_group,
)
```

调用方使用某个服务的大量接口时，以带有业务含义的名称导入整个模块：

```python
from hikari_bot.services import mycard as mycard_service
```

调用时保留服务命名空间：

```python
records = await mycard_service.fetch_player_history(user_id)
```

只有在表达服务角色或避免名称冲突时才使用别名。禁止使用通配符导入。

## 文件及职责

- `hikari_bot/plugins/base.py`：显式导入实际使用的白名单操作。
- `hikari_bot/plugins/mycard_query.py`：通过 `mycard_service` 使用 MyCard 服务。
- `hikari_bot/plugins/ygocard_query.py`：通过 `card_service` 使用卡片服务。
- `hikari_bot/plugins/ygomatch_query.py`：通过 `match_service` 和 `deck_service` 使用比赛与卡组服务。
- `hikari_bot/services/ygodeck.py`：直接导入使用到的常量、日志函数和卡图依赖，并统一使用 `io.BytesIO`。
- `hikari_bot/plugins/web/routes/sms.py`：直接导入 `log_message`。
- 其他涉及的模块：仅删除经静态检查确认未使用的导入。

`hikari_bot/plugins/monitors/__init__.py` 保持不变。该文件中的导入是为了执行插件模块，使其中的 Matcher、定时任务和生命周期钩子完成注册。

## 插件注册

继续保留现有的 `plugin_dirs = ["hikari_bot/plugins"]` 配置。本次不改为显式插件注册，因为项目会频繁增加插件，目录自动发现可以减少额外的注册维护工作。

## 验证标准

满足以下条件时，本次改动才算完成：

- 所有 Python 源文件中都不存在 `from ... import *`。
- `pyflakes` 不再报告未定义名称或通配符导入警告。
- `bot.py`、`hikari_bot` 和 `scripts` 均能通过 `compileall`。
- 现有测试全部通过。

当前失败的 `pyflakes` 检查结果作为本次纯导入改动的回归基线；不额外增加 AST 风格测试。

## 不在本次范围内的事项

- 不改变任何命令、定时任务、路由或应用行为。
- 不调整插件目录结构。
- 不建立显式插件注册表。
- 不重新设计服务接口。
- 不引入依赖注入框架。
