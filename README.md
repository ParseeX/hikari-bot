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

## 中译中

发送 `中译中 要翻译的话`，先将中文翻译成日语，再翻译回简体中文，只回复最终中文。
复用现有翻译插件的接口、代理和机器人选择配置；群聊遵循 Bot 白名单。
原有 `翻译`、`中译日`、`日译中` 等命令继续可用。

## 生产环境配置

### MyCard 前 500 玩家对局录像与卡组归档

设置 `MYCARD_CAPTURE_ENABLED=true` 后，Bot 监控全平台房间，仅在双方至少一人的当前竞技场排名位于前 500 时使用正常 MyCard 账号观战。
排名通过官方 `arena/user` 的 `arena_rank` 字段查询，缓存 5 分钟；排队后入场前重新检查缓存是否过期。
双方均在范围外时不观战、不发送归档通知；排名查询失败时暂不入场并重试，不能把未知名次视为符合条件。
对局结束后自动保存录像和卡组，不再私聊发送提取结果，也不补发历史待投递通知；卡组通过查询命令提取。
通知不附带归档编号、哈希、文件命令、卡片数量或 Side 说明。诊断详情和文件编号仅保留在内部归档中。
归档独立保存在 `MYCARD_ARCHIVE_DIR`（默认 `~/.local/share/hikari-mycard`），不进入 Git，也不会随部署清理。
保留服务端原始 `.yrp`、每小局 `.ydk`、每位玩家合并后的 `.ydk`、`manifest.json` 和 SQLite 投递记录。

观战凭据由 `MYCARD_CREDENTIALS_FILE` 指向外部 JSON 文件，支持 `username` / `u16_secret` 字段，
或使用 `token` 字段从官方认证接口刷新观战身份。凭据不得提交或写进日志，Linux 文件权限应为 `0600`。
没有新增生产依赖。

管理员私聊命令：

- `添加账号 <MyCard用户名>` / `删除账号 <MyCard用户名>`：管理开局关注名单，使用完整用户名精确匹配，即时生效。关注账号开局时，向 `SUPERUSERS` 私聊发送对手最新已收录的卡组图片；未收录时提示“对手用户名：暂无卡组”。图片可能与本场实际使用的卡组不同。
  名单单独保存在归档目录的 `watched-accounts.json`，重启保留，不修改卡组库。首次连接和重连不补发进行中对局，同一开局事件去重；删除账号会取消尚未发送的通知。该功能独立于“切换通知”和前 500 名采集筛选。
- `MyCard采集状态`：查看跟踪数、连接数、归档数、待发消息和最近异常。
- `MyCard文件 <归档编号>`：发送 ZIP，包含录像、各小局 YDK、合并 YDK 和说明；单次上限 8 MiB。
- `MyCard规则检查`：验证归档目录中的 `deck-rules.json`，每场结束自动重读，无需重启。

群聊和私聊查询命令（遵循 Bot 原有群白名单）：

- `卡组查询 <MyCard用户名>`：仅 `SUPERUSERS` 可用，复用卡组图片生成器返回一张最新卡组图片，信息区两行分别为玩家 ID 和卡组名称，不带排名、更新时间或链接。包含主卡、额外和已知 Side。
- `前排卡组信息`：所有用户可用，按当前竞技场前 100 名顺序合并转发卡组链接，未收录的玩家显示“暂无卡组”；榜单缓存 1 分钟。
两种查询实时读取当前分类映射：单人查询在图片中分两行显示 ID 和卡组名称，未识别时名称显示“未识别”；前排查询使用 `玩家名【分类甲分类乙】`。主卡组恰好 60 张且已识别时，名称前加 `60`，额外和 Side 不计入张数。分类按主卡组命中张数从少到多拼接，同名卡的多张实卡全部计数；同一分类多条规则的重叠卡不重复加分，张数相同时保持映射顺序。

前排查询继续返回 `https://ygo.xyk.one/mc/<12位随机短码>`，每个玩家固定一个短码，始终临时跳转到其最新 OURYGO 卡组链接。
短码与用户名的映射保存在归档目录下独立的 `short-links.sqlite3`，随归档一起备份，重启和换卡组不会更换短码。
网站无需登录、不再校验前 100 名，只接受已生成的短码；原 `/mc/<MyCard用户名>` 入口关闭，未知短码或尚未收录卡组返回 404，读取失败返回 503。
持有短链接的人均可访问；网站不提供按用户名生成短码的接口。`/mc` 保留给后续工具入口。
极少数单场 Side 观测异常超出容量的情况会列出全部链接供选择。Bot 的个人查询仍仅管理员可用。
Caddy 在 `ygo.xyk.one` 的静态文件兜底之前增加 `handle /mc/* { reverse_proxy 127.0.0.1:7410 }`，保留路径转发。

玩家卡组库保存在同一 `archive.sqlite3` 的新增表中，逐场保存卡组快照。
主卡和额外相同且合并后的 Side 不超过 15 张时继续补全；合并超过 15 张（含同名卡张数）视为 Side 已调整，从本场观测开始新版本，保留旧版本。历史快照按相同规则只读重建，不改写原始记录。
卡组链接、YDK 和旧归档下载使用 [YGOPro 整理卡组顺序](https://github.com/Fluorohydride/ygopro/blob/bea3249a3124cd1e5873ae8a08182778956af8da/gframe/data_manager.cpp#L532)：各分区先按怪兽、魔法、陷阱及子类型排序，怪兽再按等级／阶级／Link 值、攻击力、防御力降序，最后按卡号升序。排序读取现有卡片主库；缺少资料的卡按卡号排在末尾。原始录像、历史 YDK 和卡组版本比较不受影响。
启动时自动补录已完成的旧归档，不重发旧消息；失败且没有卡组的对局不会清空已有记录。
主卡与额外按卡号及张数比较，顺序不影响相等判断：两者均相同才合并 Side，
每张 Side 卡保留历次观察到的最大张数；主卡或额外任一变化即另存新版本。
查询返回按录像时间排列的最后一个连续版本，旧版本继续保留；换回以前的配置也重新累计 Side。
每个版本记录首次观察和最近更新时间。跨场合并 Side 超过 15 张时从本场开始新版本，旧版本保留。

Side 推断以每位玩家最早收到的小局录像为基准：比较后续小局主卡与额外卡组的卡号数量，
各卡取相对基准的最大正差。例如基准 1 张、后来 3 张，则记录 2 张已观察 Side；
不会重复累加多局换入的同一张卡，也不会把基准中换出的卡重复记入 Side。
这些卡只是不完整的 Side 证据，无法恢复从未换入的卡；若缺少首局，基准也可能已是换卡后的卡组。
YDK 各分区使用上文的 YGOPro 排序规则。
OURYGO 链接直接编码卡组，不上传文件；站点当前使用 HTTP。

卡组分类管理页：`https://ygo.xyk.one/mc/admin`，使用独立管理员密码登录。按系列关键词或卡名搜索会列出全部匹配卡片，可连续点选，结果和关键词保留；也支持按卡号定位，以及添加、编辑、删除映射和筛选。保存后采集端无需重启。
在可信本机终端运行 `.venv\Scripts\python.exe scripts/mycard_admin_password.py --ssh server` 设置或重置密码。密码隐藏输入，仅将 scrypt 哈希通过 SSH 保存到归档目录 `admin-password.json`（权限 0600），不要把密码写入聊天或 Git。重置后旧会话失效，旧哈希保留在同目录 `admin-password.previous.json` 供回滚。
管理会话保存在独立 `admin-sessions.sqlite3`，12 小时到期；浏览器使用 Secure、HttpOnly、SameSite Cookie，写操作校验同源。密码尚未配置时禁止登录和编辑。
规则保存在 `deck-rules.json`，每次管理页保存前备份到 `deck-rules.json.bak`；并发编辑发生冲突时拒绝覆盖，需刷新后重试。管理页不改写已有归档。
识别规则默认 `{"version":1,"rules":[]}`，新归档在 `classification.labels` 保存所有命中的分类，在 `classification.evidence` 保存命中卡号。也可按下面格式补充：

```json
{
  "version": 1,
  "rules": [
    {"label": "系列名称", "cards": [12345678], "match": "any"},
    {"label": "组件名称", "cards": [23456789, 34567890], "match": "any"}
  ]
}
```

示例卡号仅示意，需替换为实际映射。规则仅检查主卡组，额外和 Side 不参与；统一按任一卡命中，不再提供“包含全部”。兼容读取旧文件中的 `match: all`，但同样按任一卡匹配。标签按命中张数升序排列，并在清单中保留实际命中卡号和张数。
匹配前按卡片主库中显式关联的同一卡片身份归一化异画和临时编号，原卡与异画可命中同一规则；不凭卡名相似推断。原始录像、YDK 和命中证据保留实际卡号，未知卡号仍按原编号匹配。

活跃对局没有 30 分钟或其他总时限。默认最多同时连接 8 场、每 0.5 秒入场一场，
超额房间排队；`MYCARD_CAPTURE_CONCURRENCY` 和 `MYCARD_JOIN_INTERVAL` 可调整。
入场握手逐个进行，确认观战身份后即可并行等待各自对局结束。连续 3 次握手失败后，
所有房间共享 5 分钟冷却；每次恢复仅试一条连接，失败后延长到 10、20、最多 30 分钟，成功则恢复正常入场。
握手等待上限为 15 秒，仅约束入场阶段，不限制已进入的对局时长。
`MYCARD_SPECTATOR_HOST` / `MYCARD_SPECTATOR_PORT` 默认是 `tiramisu.moecube.com:8911`。
使用自己有权管理的服务器做 SSH 转发时，可改为回环地址上的转发端口；该设置只影响观战 TCP，
账号认证、房间列表、归档和私聊仍按原路径运行。隧道应仅监听回环地址，并保留原有并发限制。
MyCard 服务端对同一 IP 最多允许 10 条连接（见 [服务端实现](https://github.com/mycard/ygopro-server/blob/master/ygopro-server.js)），
因此普通观战方案只能监控全量房间，不能保证全量录像覆盖。不要将并发调高来尝试越过服务端限制；
实现真正全量需要平台提供录像接口或授权。默认留出 2 条同一出口 IP 的连接余量。
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
