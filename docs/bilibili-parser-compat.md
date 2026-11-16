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
