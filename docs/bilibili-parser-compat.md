# Bilibili parser compatibility

The runtime uses `nonebot-plugin-parser==2.6.6` (the version in `uv.lock`).
`hikari_bot/plugins/parser_compat.py` loads a repository-owned adapter; upstream
site-packages are not modified. There is no upstream configuration for disabling
only Bilibili video cards in this version.

The adapter changes only:

- Media requests to `bilivideo.com` and its subdomains get `Range: bytes=0-`.
  Existing Range headers are preserved. The CDN returned HTTP 567 without Range,
  but 206 with Range for the same failing URL on 2026-11-14.
- Direct Bilibili video results skip the summary card and use the original media
  renderer. Video thumbnails, media failure notices, size/duration limits and
  audio/video merging are unchanged. Dynamic posts, image/text posts, reposts,
  other platforms and parser commands keep their existing behavior.

The two wrapped upstream methods are `StreamDownloader._download_file` and
`ImageRenderer.render_messages`. On a parser upgrade, inspect these signatures,
run `python -m pytest tests/test_parser_compat.py`, and verify one real media
download plus rendering with the installed dependency. An upgrade warning is
logged for versions other than 2.6.6. Do not assume a package upgrade alone fixes
CDN errors.

Rollback: remove only `hikari_bot/plugins/parser_compat.py` from the deployed
plugin directory and restart `bot.service`. The service helper and tests are
inert without that plugin. No configuration migration is required.

## 官方 QQ 的转发视频入口（2026-11-17）

官方 [群消息结构](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
将卡片地址放在 `ark_data.fields.jump_url`，转发消息可放在 `msg_elements`。
适配器 1.7.4 的普通正文转换不会展开这些字段，嵌套 `QQReplyMessage` 还会丢弃卡片数据。
因此直接转发可能把外层 QQ 分享地址交给解析器，得到“暂无可用解析器”。

官方入口现在在 SDK 转换前有界提取 B 站视频/短链接，沿用原下载流程，群聊无需 @，
私聊相同。只处理已识别视频，保留 SDK 的作者、会话和回复目标验证；不更改第三方包，
不执行卡片标题/转发内容里的命令，也不展开并行消息（101）。多条转发仅处理首个识别到的视频链接，
不承诺下载整个聊天记录中的所有视频。深度超过 4 层或第 64 个以后的元素不扫描。

带 B 站来源标签但没有可用地址的卡片，按用户于 2026-11-17 确认的方案搜索完整标题。
检查前三页、每页 20 条，去重后只有一条完整同名结果就直接下载，多条时提供作者/时长
按钮（每页 8 条、只允许发起者、10 分钟过期）；按钮长标签最多 24 字，正文保留完整信息。
搜索结果唯一不代表全站唯一，可能命中同名或搬运视频，不能保证与原卡片对应。
无完整同名结果、缺少标题或搜索失败时明确提示需要原视频链接；不自动下载模糊匹配结果。
未知来源卡片和普通链接的处理不变。解析关闭开关、事件去重、体积/时长限制及
“直接发视频，不先发预览卡片”的既有修复保留。

回归入口：`tests/test_official_shared_video.py`，覆盖原始 Dispatch、群聊/私聊、
嵌套及扁平转发、缺失地址、域名伪装、解析关闭、重复事件和命令隔离。
这些模拟测试不代表真实 QQ 卡片已验收；客户端仍需用户转发验证。
回退官方修复时切回发布前的 `srbs/current` 版本并重启 `srbs`；原 OneBot 服务与数据不变。

用户反馈 QQ 小程序卡片仍提示缺少链接后，已对比原 OneBot 生产环境的
`nonebot-plugin-parser 2.6.6`：其入口从 `meta.detail_1.qqdocurl`、
`meta.news.jumpUrl`、`meta.music.jumpUrl` 取址，下载器并不靠标题搜索视频。
官方入口兼容卡片或 fields 中实际存在的 meta/raw，保留旧分支优先顺序，
仍只接受 B 站视频地址。此兼容测试通过不代表官方确实下发这些旧字段，
用户两次实际转发确认此类官方小程序卡片仅带 title/source/preview/source_logo，
没有 jump_url、meta/raw 或 BV 号；据此改为上述经用户确认的标题搜索方案。
这属于替代体验，不宣称恢复了旧版按原地址精确定位的行为。

按需诊断 `share_probe.py` 默认关闭。运维可把未来一小时以内的 Unix 到期时间写入
`~/.local/share/srbs/shared-video-probe-until`，每个进程最多记录三张缺失地址卡片的
`SRBS_SHARE_SHAPE`。只记录固定字段名、类型及地址类别，不记录正文、标题、用户、
群、完整 URL 或参数；删除标记立即关闭，到期也会自动关闭。此信息用来确认上游
是否提供旧字段，不以增加猜测或搜索策略替代真实载荷验证。
