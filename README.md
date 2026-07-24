# hikari-bot
A yu-gi-oh bot for qq group 457767939

## 安装与启动

项目支持 Python 3.10～3.13，并使用 [uv](https://docs.astral.sh/uv/) 管理依赖。

安装开发环境：

```powershell
uv sync
uv run playwright install chromium
```

启动机器人：

```powershell
uv run python bot.py
```

生产环境不安装开发工具：

```powershell
uv sync --no-dev
uv run playwright install chromium
uv run python bot.py
```

运行 B 站扫码登录脚本时，额外安装可选依赖：

```powershell
uv sync --extra bili-login
uv run python scripts/bili_login.py
```

修改 `pyproject.toml` 中的依赖后运行 `uv lock`，并同时提交
`pyproject.toml` 与 `uv.lock`。可用以下命令检查锁文件是否为最新：

```powershell
uv lock --check
```

Playwright 的 Chromium 浏览器不包含在 Python 锁文件中，因此每个新环境都需要单独执行一次
`uv run playwright install chromium`。

卡组图片渲染使用 CairoSVG，它还需要操作系统提供 Cairo 动态库：

- Debian/Ubuntu 安装 `libcairo2`。
- Windows 安装包含 `libcairo-2.dll` 的 Cairo/GTK 运行库，并将 DLL 目录加入
  `PATH` 或 `CAIROCFFI_DLL_DIRECTORIES`。

## 生产环境配置

服务默认读取项目根目录的 `.env.prod`。该文件包含密钥，已被 Git 忽略；
请从 `.env.example` 复制后填写，不要提交真实值。也可以在启动进程中设置
`HIKARI_ENV_FILE=/absolute/path/to/.env.prod` 指定其他位置。

现有部署至少应配置：

```dotenv
ONEBOT_ACCESS_TOKEN=replace-me
SUPERUSERS=["你的QQ号"]
CARDRUSH_UPLOAD_TOKEN=replace-me
UPTIME_TOKEN=replace-me
JIHUANSHE_TOKEN=replace-with-a-fresh-token
API_TIMEOUT=120.0
PARSER_DISABLED_PLATFORMS=["twitter", "douyin"]
```

按启用功能追加以下配置：

```dotenv
CARDRUSH_PROXY_URL=socks5h://127.0.0.1:1080
PUBLIC_GROUP_ID=群号
BLOG_DEPLOY_SCRIPT=/absolute/path/to/deploy.sh
BLOG_UPDATE_SCRIPT=/absolute/path/to/update.sh
JM_DATA_DIR=/absolute/path/to/jm-data
JM_PDF_PASSWORD=replace-me
```

`JIHUANSHE_TOKEN` 和 `JM_PDF_PASSWORD` 曾经硬编码在仓库中，部署时请使用新值，
不要继续使用 Git 历史中出现过的旧值。未配置某项可选集成时，对应功能会给出配置错误，
不会让整个 Bot 在启动阶段失败。

## GitHub Actions 自动部署

`main` 分支 push 后，GitHub Actions 会通过 SSH 执行服务器上的
`$HOME/hikari-bot/update.sh`。请在仓库 Settings → Secrets and variables → Actions
中配置：

- `SERVER_HOST`：服务器地址
- `SERVER_USER`：SSH 用户
- `SERVER_SSH_KEY`：SSH 私钥
- `SERVER_PORT`：可选 SSH 端口，未配置时使用 22

服务器上需要先将本仓库部署到 `$HOME/hikari-bot`，并保证以下文件可执行：

```text
$HOME/hikari-bot/update.sh
```

脚本默认进入 `$HOME/hikari-bot`，也支持通过 `HIKARI_BOT_DIR` 指定其他项目目录；
它会同步 `origin/main`、执行 `uv sync --no-dev`，并重启 `bot.service`。
SSH 用户需要被授予执行 `sudo systemctl restart bot.service` 的权限，建议只允许
该条 systemd 重启命令，不要授予完整 sudo 权限。

专为游戏王玩家设计的 QQ Bot，主要支持以下功能：

## 🎴卡片查询功能
- 随机一卡 - 随机展示一张卡片
- 每日一卡 - 为每个用户生成专属的每日卡片
- 查卡图/卡图 - 通过卡片名称或ID查看卡片图像
  - 支持模糊搜索
  - 支持异画搜索
  
- 查效果 - 查询卡片效果文本
- 查卡密 - 查询卡片密码（ID）
- 卡价查询 - 支持查询日本卡价信息
- 支持价格比较和保存功能

## 🏆 MyCard 对战平台功能
### 历史查询
- 绑定 [用户ID] - 绑定 MyCard 账号
- 历史查询
- 首胜查询/首赢查询 - 查询首胜状态
- 查询绑定/绑定查询 - 查看已绑定的账号
- 胜率查询/胜率统计 - 统计胜率信息
- 标签管理 - 添加标签、删除标签、查看标签
- 支持生成胜率统计图表
### 实时订阅
- 订阅 - 订阅指定玩家的对局信息
- 退订 - 取消订阅
- 实时WebSocket连接监控对局状态
- 自动发送对局开始通知
- 支持群组和私聊通知

## 🎯 比赛相关功能
### 比赛查询
- 比赛查询 [关键词] - 搜索相关比赛信息
- 支持"神人杯"特殊查询
- 显示比赛详情、报名人数、奖励等信息
- 支持比赛报名状态查询

## 🃏 卡组相关功能
- 提供Web端卡组PDF生成服务
- 自动生成PDF格式的卡组列表
  
## 📱 通用功能
### 系统管理
- 重载插件 - 从Git拉取最新代码并重启
- Bot上线通知功能
- 接收手机短信转发
## 🎮娱乐功能
- jm [ID] - 下载JM漫画
- 支持私聊和群文件发送
