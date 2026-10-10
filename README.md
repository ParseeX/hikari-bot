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

### MyCard 前 500 玩家对局录像与卡组归档

设置 `MYCARD_CAPTURE_ENABLED=true` 后，Bot 监控全平台房间，仅在双方至少一人的当前竞技场排名位于前 500 时使用正常 MyCard 账号观战。
排名通过官方 `arena/user` 的 `arena_rank` 字段查询，缓存 5 分钟；排队后入场前重新检查缓存是否过期。
双方均在范围外时不观战、不发送归档通知；排名查询失败时暂不入场并重试，不能把未知名次视为符合条件。
对局结束后，向 `SUPERUSERS` 私聊发送双方 OURYGO 卡组链接、归档编号和采集异常说明。
归档独立保存在 `MYCARD_ARCHIVE_DIR`（默认 `~/.local/share/hikari-mycard`），不进入 Git，也不会随部署清理。
保留服务端原始 `.yrp`、每小局 `.ydk`、每位玩家合并后的 `.ydk`、`manifest.json` 和 SQLite 投递记录。

观战凭据由 `MYCARD_CREDENTIALS_FILE` 指向外部 JSON 文件，支持 `username` / `u16_secret` 字段，
或使用 `token` 字段从官方认证接口刷新观战身份。凭据不得提交或写进日志，Linux 文件权限应为 `0600`。
没有新增生产依赖。

管理员私聊命令：

- `MyCard采集状态`：查看跟踪数、连接数、归档数、待发消息和最近异常。
- `MyCard文件 <归档编号>`：发送 ZIP，包含录像、各小局 YDK、合并 YDK 和说明；单次上限 8 MiB。
- `MyCard规则检查`：验证归档目录中的 `deck-rules.json`，每场结束自动重读，无需重启。

Side 推断以每位玩家最早收到的小局录像为基准：比较后续小局主卡与额外卡组的卡号数量，
各卡取相对基准的最大正差。例如基准 1 张、后来 3 张，则记录 2 张已观察 Side；
不会重复累加多局换入的同一张卡，也不会把基准中换出的卡重复记入 Side。
这些卡只是不完整的 Side 证据，无法恢复从未换入的卡；若缺少首局，基准也可能已是换卡后的卡组。
YDK 目前各分区按卡号升序，不宣称与官方怪兽／魔法／陷阱排序一致。
OURYGO 链接直接编码卡组，不上传文件；站点当前使用 HTTP。

识别规则默认 `{"version":1,"rules":[]}`，没有规则时显示“未识别”。可按下面格式补充：

```json
{
  "version": 1,
  "rules": [
    {"label": "系列名称", "cards": [12345678], "match": "any"},
    {"label": "组件名称", "cards": [23456789, 34567890], "match": "all"}
  ]
}
```

示例卡号仅示意，需替换为实际映射。规则只检查主卡与额外卡组，`any` 表示任一卡命中，
`all` 表示全部命中；标签按规则顺序去重后以 `＋` 拼接，并在清单中保留命中卡号。

活跃对局没有 30 分钟或其他总时限。默认最多同时连接 8 场、每 0.5 秒入场一场，
超额房间排队；`MYCARD_CAPTURE_CONCURRENCY` 和 `MYCARD_JOIN_INTERVAL` 可调整。
MyCard 服务端对同一 IP 最多允许 10 条连接（见 [服务端实现](https://github.com/mycard/ygopro-server/blob/master/ygopro-server.js)），
因此普通观战方案只能监控全量房间，不能保证全量录像覆盖。不要将并发调高来尝试越过服务端限制；
实现真正全量需要平台提供录像接口或授权。默认留出 2 条连接余量供正常客户端使用。
房间消失后最多额外等待 30 秒接收尾部录像，这不是活跃对局超时。
禁止观战、观战账号本人参与、排队期间结束或连接失败的房间会标明未取得／不完整。
本功能不补抓启动前已结束的历史对局，也不能保证服务器向观战者提供每一小局。
重连按录像内容去重，重启恢复已落盘文件；私聊失败持久化重试。
若 QQ 已收到而进程在写入成功回执前退出，重启后可能重复发送，不能保证严格一次投递。
停用只需将 `MYCARD_CAPTURE_ENABLED=false` 并重启，归档文件保留；默认不自动删除历史文件。

服务默认读取项目根目录的 `.env.prod`。该文件包含密钥，已被 Git 忽略；
请从 `.env.example` 复制后填写，不要提交真实值。也可以在启动进程中设置
`HIKARI_ENV_FILE=/absolute/path/to/.env.prod` 指定其他位置。

现有部署至少应配置：

```dotenv
ONEBOT_ACCESS_TOKEN=replace-me
SUPERUSERS=["你的QQ号"]
UPTIME_TOKEN=replace-me
JIHUANSHE_TOKEN=replace-with-a-fresh-token
API_TIMEOUT=120.0
PARSER_DISABLED_PLATFORMS=["twitter", "douyin"]
```

按启用功能追加以下配置：

```dotenv
CARDRUSH_PROXY_URL=socks5h://127.0.0.1:1080
PUBLIC_GROUP_ID=群号
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
- 卡价查询 / 卡价 - 输入卡名关键词或卡密，自动选取匹配度最高的卡片；支持别名、部分卡名及少量错字。选择罕贵后，同时显示集换社人民币最低价、集换价与 Cardrush 日元买取价；不同卡包版本分别列出
- 日版价格查询 / 日版卡价查询 - 选择罕贵后，按卖家备注匹配日版最低在售价，同时显示 Cardrush 日元买取价
- 集换社桥接部署与查询说明见 [手机桥接文档](scripts/jihuanshe_bridge/README.md)
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
