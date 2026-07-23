# FAQ、帮助功能与配置清理设计

## 目标

彻底删除当前机器人中的 FAQ/裁定查询和图片帮助功能，同时收紧生产配置边界：

- `COMMAND_START` 固定为 `{"", "/"}`，不再通过环境变量配置。
- 卡表网站固定为 `https://ygo.xyk.one/deck`，不再通过环境变量配置。
- 删除仅供 FAQ 中转使用的 `FAQ_RELAY_GROUP_ID`。
- 删除 FAQ/裁定查询的命令、网络访问代码、文档和帮助内容。
- 删除帮助命令及其静态资源和生成配置。

## 范围

### 配置

`hikari_bot/core/config.py` 的 `Settings` 不再包含：

- `command_start`
- `faq_relay_group_id`
- `public_deck_url`

如果 `_optional_int()` 不再有其他调用，则一并删除。`bot.py` 直接把
`{"", "/"}` 传给 `nonebot.init()`。

卡表网站地址作为产品常量放在 `hikari_bot/core/constants.py`，比赛插件直接引用。

`.env.example` 和 README 中同步删除上述三个环境变量，避免服务器配置继续携带无效值。

### FAQ/裁定查询

从 `hikari_bot/plugins/ygocard_query.py` 删除：

- `裁定查询` 命令
- `游戏王裁定`、`裁定`别名
- FAQ 中转消息组装和发送逻辑

从 `hikari_bot/services/ygocard.py` 删除：

- FAQ URL 常量
- `get_qa_by_id()` 网络访问和 HTML 解析逻辑
- 删除后不再需要的 `NavigableString` 导入

README 中删除“查裁定”功能说明。

### 帮助功能

从 `hikari_bot/plugins/base.py` 删除：

- `帮助`/`help` 命令
- 读取、Base64 编码和发送 `help.png` 的代码
- 删除后不再需要的 `base64`、`RESOURCES_DIR` 等导入

删除以下资源：

- `hikari_bot/resources/help.png`
- `hikari_bot/resources/help.md`
- `.crossnote/` 下用于生成旧帮助图片的配置和样式

未来文档网站作为独立功能重新接入，本次不保留占位命令或功能开关。

## 测试设计

先添加不依赖 NoneBot 的回归测试，并确认它在清理前失败：

1. `Settings` 不再声明三个被移除的配置字段。
2. `bot.py` 仍固定包含空前缀和 `/` 前缀。
3. 卡表网站常量等于 `https://ygo.xyk.one/deck`。
4. Python 源码中不再注册 `帮助`、`help`、`裁定查询`、`游戏王裁定`、`裁定`命令。
5. FAQ 服务函数及 URL 常量不存在。
6. 旧帮助资源和 `.crossnote` 目录不存在。
7. README 和 `.env.example` 不再包含已删除的功能或配置项。

实现完成后运行：

- 定向 pytest 回归测试
- 全仓库 `compileall`
- 全仓库硬编码与残留文本扫描
- `git diff --check`

当前执行环境没有安装 NoneBot，因此完整进程启动测试留给服务器部署前执行；测试本身只依赖
Python 标准库和当前已安装的 `python-dotenv`。

## 非目标

- 不新增文档网站或新的帮助命令。
- 不调整其他卡片查询功能。
- 不重构现有星号导入或修复与本次删除无关的静态检查问题。
- 不改变管理员、Token、代理、博客和 JM 等其余生产配置。
