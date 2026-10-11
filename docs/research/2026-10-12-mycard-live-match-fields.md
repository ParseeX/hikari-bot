# MyCard 进行中对局可获取的信息

核验日期：2026-10-12。范围为当前公开房间列表和官方公开源码；没有新增账号观战连接、读取凭据或修改生产服务。协议推断与现网响应分开标注。

## 1. 当前公开房间列表：全局概览，不含实时局面

本次只读连接 `wss://tiramisu.moecube.com:8923/?filter=started`，初始 `init` 有 121 个房间、242 个玩家条目；随后约 12 秒观察到 `create` 与 `delete`。所有房间均只有 `id`、`title`、`user`、`users`、`options`、`arena`，玩家条目只有 `username`、`position`。这是当次样本，不保证所有节点或未来版本相同。

本次 `user` 实际为空对象 `{}`，不能把它解释为可用的账号资料；`arena` 实际为字符串。

| 信息 | 当前可得方式 / 限制 |
| --- | --- |
| 房间标识、标题 | `id`、`title` |
| 双方玩家名、座位 | `users[].username`、`users[].position` |
| 房间类别 | `arena`；具体含义应结合当前节点实际取值 |
| 新出现、消失的进行中房间 | `create` / `delete`；初次 `init` 是当时快照 |
| 单局 / Match / Tag 模式、规则 | `options.mode`、`duel_rule`、`rule`、`lflist` |
| 是否禁止观战 | `options.no_watch`；允许不等于握手必然成功 |
| 初始 LP、起手数、每回合抽卡数、规则时间限制 | `start_lp`、`start_hand`、`draw_count`、`time_limit`，**不是实时 LP / 手牌数 / 剩余秒数** |
| 洗牌 / 卡组检查设置、加时、录像模式 | `no_shuffle_deck`、`no_check_deck`、`auto_death`、`replay_mode` |
| 实际开局时间、当前 LP、回合、阶段、比分、第几小局、观战人数 | 本次公开 WS 中均不存在 |
| 玩家排名、D.P、完整卡组 / Side | 本次公开 WS 中不存在；排名需另查玩家 / 榜单接口，已有卡组库也不保证就是本场配置 |

样本房间 `mode=1`、`duel_rule=5`、`start_lp=8000`、`start_hand=5`、`draw_count=1`、`time_limit=240`、`no_watch=false`、`replay_mode=2`。公开源码构造 WS 对象的字段与实测一致；`filter=started` 由 `duel_stage != 0` 筛选，开始时广播 `create`，删除时广播 `delete`。[WS 对象与事件实现](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/roomlist.js#L13-L98)

官方服务端内部确实维护 `room.start_time`，开局还向竞技场内部 `room-start` 上报，但没有把该值放入上述 WS 响应。因此不能用“源码里有 start_time”宣称公开接口能拿到。持续监听的 `create` 接收时间可以近似监控到开局的时间；初次快照中的旧房间只能记首次发现时间。[开局内部处理](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L4253-L4293)

另对一位公开在赛玩家查询现有 `arena/user?username=...`，返回 HTTP 200，字段含 `exp`、`pt`、`exp_rank`、`arena_rank`、竞技与娱乐胜 / 负 / 平 / 总数 / 胜负比。这能关联玩家积分、排名及累计战绩，**不是本场实时比分或结算结果**；排名也会随时间变化。

## 2. 官方另有 HTTP 房间列表，但部署开关决定可见内容

SRVPro 的 `/api/getrooms` 在 `public_roomlist=true` 时可以不带管理凭据读取；否则返回要求认证的占位房间。另一个开关 `show_info` 决定是否输出 `users[].status.score/lp/cards`，以及 `istart` 中的 `Duel:N Turn:N` / `Siding`。它不能被当作“当前 MyCard WS 已返回这些字段”。玩家 IP 另需 `show_ip` 与认证同时成立，不属于公开观战信息。[HTTP 接口及字段](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L5217-L5261)

HTTP 与 HTTPS 使用同一个请求处理器，WS 挂在启用 SSL 时的 HTTPS 服务上；因此可对当前 WS 对应主机做一次普通 HTTPS `GET /api/getrooms` 核验，不应猜测后台密码或绕过认证。[监听配置](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L824-L839)

**现网核验：** 一次无凭据请求 `https://tiramisu.moecube.com:8923/api/getrooms` 返回 HTTP 200，但 `rooms` 只有一条占位对象，字段为 `roomid`、`roomname`、`needpass`，`roomname="密码错误"`，没有 `users` 或 `status`。所以此路径当前不能无凭据提供上述详细状态；未进一步尝试认证。HTTP 200 不代表查询成功。

## 3. 正常观战的数据流：能够进一步实现实时状态

以下是 **官方实现支持，尚未对本次生产观战连接实测解析** 的内容。需获得普通观战身份并持续解析 `STOC_GAME_MSG`，不是再读一次房间列表即可拿到。

| 信息 | 协议 / 实现依据 | 需要注意 |
| --- | --- | --- |
| 每小局初始 LP、主卡 / 额外数量 | `MSG_START` | 同时处理本小局先后手与座位交换 |
| 当前谁的回合、回合数 | `MSG_NEW_TURN` | 消息给出玩家；回合数要从该局开始累计 |
| 当前阶段 | `MSG_NEW_PHASE` | 抽卡、准备、主要、战斗、结束等 |
| 双方实时 LP | `MSG_DAMAGE` / `RECOVER` / `LPUPDATE` 等 | 维护状态，不能只处理其中一种变化 |
| 场上公开卡、公开墓地 / 除外卡、数量和位置变化 | 更新与移动消息 | 里侧 / 未公开身份会被遮蔽；数量不等于知道卡名 |
| 召唤、发动、连锁、攻击等公开操作 | `MSG_CHAINING` / `MSG_ATTACK` 等 | 可形成行动记录；效果公开前不能断言隐藏卡身份 |
| 小局胜负、胜负原因 | `MSG_WIN` | 聚合可推算比分，须正确映射先后手交换与平局 |
| 换 Side 阶段、整场结束 | `STOC_WAITING_SIDE` / `STOC_DUEL_END` | 可区分“小局结束”与“Match 结束” |
| 当前第几小局 | 累计 `MSG_START` 与 `MSG_WIN` | 完整历史流足够时可恢复；不是公开列表里的直接字段 |

源码证据：[开局包](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L600-L630)、[回合与阶段](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1156-L1197)、[LP 变化](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1530-L1571)、[连锁](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1382-L1401)、[攻击](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1640-L1659)、[胜负及座位映射](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L814-L835)、[换 Side 与整场结束](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L672-L726)。

**不能保证观战能拿到实时剩余操作时间。** 已核验的 `WaitforResponse` 只向两位参赛玩家发送 `STOC_TIME_LIMIT`，没有发送给普通 observers 或观战缓存记录器。列表 `time_limit=240` 仅是房间规则，不能当成实时倒计时。[计时发送对象](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1874-L1886)

## 4. 隐藏信息与中途加入

普通观战不应当成“双方开手”。抽卡消息在发给观众前把未公开卡号归零；手牌刷新会遮蔽非公开身份，里侧场上卡和里侧额外也会遮蔽。因此可以统计数量、记录已公开卡，但不能在实时状态中可靠列出尚未公开的完整手牌、牌库顺序、完整主卡 / 额外 / Side。[抽卡遮蔽](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1507-L1527)、[手牌遮蔽](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L2119-L2157)、[场上里侧遮蔽](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L2025-L2068)、[额外遮蔽](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L2188-L2220)

SRVPro 允许中途观战时，先回放 `watcher_buffers` 中缓存的公开观战流，再继续转发实时流。因此协议实现上可以补算当前回合、比分和小局数；必须先确认缓存完整、完成历史追赶，再把状态标成实时，不能用补发事件的接收时间当原始发生时间。[中途观战入口](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L2577-L2595)、[缓存与实时转发](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L3546-L3570)

## 5. 录像何时可得

YGOPro 引擎在每小局 `MSG_WIN` 后调用 `EndDuel`，生成该局录像；这不等于普通观众当时就收到。`replay_mode` 的 `0x2` 会禁止引擎向普通观众直接发录像，SRVPro 的 `replay_delay` 又会扣住 Match 录像，在 `DUEL_END` 时批量向玩家及观众发送。当前房间样本 `replay_mode=2` 与这个延迟路径相符。不能承诺“第一局一结束即可取得完整第一局卡组”。[每局生成](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L1829-L1853)、[延迟模式设置](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L1940-L1948)、[录像拦截](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L5101-L5112)、[整场结束发放](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L4126-L4156)、[批量发送所有录像](https://github.com/mycard/srvpro/blob/c16bbccddbd5f3304e6e1d93118db45e8868003f/ygopro-server.js#L1602-L1618)

录像内容以每局实际入场的主卡与额外为基础。未在任何一局换入的 Side 不会因此自动完整公开；现有卡组库跨局差分与跨场累积仍有必要。[入场牌组写入与加载](https://github.com/mycard/ygopro/blob/176fc60c9bb2d3ee1d984a743647629b1503d4ca/gframe/single_duel.cpp#L580-L605)

## 实现判断

现有 Bot 的观战模块着重收录像，没有把上述实时游戏消息解析成状态。可以优先在现有观战连接上增加“当前 LP、回合 / 阶段、小局 / 比分、换 Side 状态、已经公开的卡”解析，不必为了这些字段额外开第二条观战连接。该判断基于本地代码和官方协议；实现前仍需用一场正常观战验证版本兼容、历史补发和座位映射。
