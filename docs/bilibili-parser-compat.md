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
不执行卡片标题/转发内容里的命令，也不展开引用消息。多条转发仅处理首个识别到的视频链接，
不承诺下载整个聊天记录中的所有视频。深度超过 4 层或第 64 个以后的元素不扫描。

带 B 站来源标签但没有可用地址的卡片会提示“分享 → 复制链接”，不根据标题猜视频。
未知来源卡片和普通链接的处理不变。解析关闭开关、事件去重、体积/时长限制及
“直接发视频，不先发预览卡片”的既有修复保留。

回归入口：`tests/test_official_shared_video.py`，覆盖原始 Dispatch、群聊/私聊、
嵌套及扁平转发、缺失地址、域名伪装、解析关闭、重复事件和命令隔离。
这些模拟测试不代表真实 QQ 卡片已验收；客户端仍需用户转发验证。
回退官方修复时切回发布前的 `srbs/current` 版本并重启 `srbs`；原 OneBot 服务与数据不变。
