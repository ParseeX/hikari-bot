# Cardrush Single-Layer Forward Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Cardrush 多页 QQ 图报合并成一条单层转发消息，不使用中转群。

**Architecture:** 新增 OneBot 专用 `cardrush_forward.py`，直接把每页压缩图构造成自定义转发节点，并按目标调用一次私聊或群聊合并转发 API。压缩模块只保留图片处理和 1200 分类器，Cardrush 主插件负责选择发送目标。

**Tech Stack:** Python 3.10+、NoneBot 2、OneBot V11、pytest、Pillow。

## Global Constraints

- 不使用中转群，不新增或复用任何中转群配置。
- 每份图报对每个接收目标只调用一次合并转发 API。
- 每一页必须按原顺序出现在一个独立自定义节点中。
- 自动日报必须发送到所有 `ADMIN` 私聊，并在 `settings.public_group_id` 存在时发送到该群。
- 只静默处理数值型 `retcode == 1200`，不得重试。
- 非 1200 错误必须原样抛出。
- QQ 继续使用 200KB WebP，B 站继续使用原图。
- 直接在 `main` 分支修改、提交并推送。

---

### Task 1: Add the single-layer forward adapter and integrate Cardrush

**Files:**
- Create: `hikari_bot/plugins/monitors/cardrush_forward.py`
- Create: `tests/cardrush/test_qq_forward.py`
- Modify: `hikari_bot/plugins/monitors/cardrush_delivery.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py`
- Modify: `tests/cardrush/test_qq_delivery.py`
- Modify: `tests/cardrush/test_plugin_import.py`

**Interfaces:**
- Consumes: compressed `Sequence[bytes]`, a OneBot V11 `Bot`, and exactly one target ID.
- Produces: `send_qq_forward(bot, pages, *, user_id=None, group_id=None, log_prefix) -> bool`.

- [x] **Step 1: Write failing forward behavior tests**

Create a test module that asserts the adapter file exists, then loads it with `importlib`. Use a fake bot
whose `call_api` records calls. Verify private routing, group routing, two ordered image nodes, real
`ActionFailed(retcode=1200)` suppression, and non-1200 propagation. Update the source contract to
expect two `await send_qq_forward(` calls and no `await send_qq_pages(` calls.

- [x] **Step 2: Run tests to verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush/test_qq_forward.py tests/cardrush/test_plugin_import.py -q
```

Expected: failure because `cardrush_forward.py` does not exist and Cardrush still calls
`send_qq_pages`.

- [x] **Step 3: Implement the timeout classifier**

In `cardrush_forward.py`, keep OneBot error inspection private to avoid importing the side-effectful
`monitors` package during isolated adapter tests:

```python
def _is_qq_send_timeout(error: Exception) -> bool:
    info = getattr(error, "info", None)
    retcode = (
        info.get("retcode")
        if isinstance(info, Mapping)
        else getattr(error, "retcode", None)
    )
    return retcode == 1200
```

Delete `send_qq_pages` after both production call sites have migrated.

- [x] **Step 4: Implement one-layer forward delivery**

In `cardrush_forward.py`, encode each page and construct:

```python
MessageSegment.node_custom(
    user_id=int(bot.self_id),
    nickname=f"Cardrush 图报 {index}/{total}",
    content=Message(
        MessageSegment.image(f"base64://{encoded}")
    ),
)
```

Validate that exactly one target is set, call the corresponding forward API once, return `False` after
logging a 1200 response, and re-raise every other exception.

- [x] **Step 5: Integrate manual and automatic reports**

For the manual handler, pass `event.group_id` when present and otherwise `event.user_id`. For the
automatic task, pass each administrator as `user_id`. Keep `prepare_qq_pages` and
`post_article_with_images(screenshots, date_str)` unchanged.

- [x] **Step 6: Run focused and full verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/cardrush -q --basetemp=.pytest_cache/tmp-cardrush-forward
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_cache/tmp-cardrush-forward
.\.venv\Scripts\python.exe -m compileall -q bot.py hikari_bot scripts
.\.venv\Scripts\python.exe -m pyflakes hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py hikari_bot/plugins/monitors/cardrush_forward.py tests/cardrush/test_qq_delivery.py tests/cardrush/test_qq_forward.py tests/cardrush/test_plugin_import.py
@'
import nonebot
nonebot.init()
plugin = nonebot.load_plugin(
    "hikari_bot.plugins.monitors.cardrush"
)
assert plugin is not None
print("cardrush_plugin_load=ok")
'@ | .\.venv\Scripts\python.exe -
```

Expected: all commands exit with code 0 and `cardrush.py` remains below 460 lines.

- [x] **Step 7: Commit and push**

```powershell
git add docs/superpowers/specs/2026-07-23-cardrush-single-layer-forward-design.md docs/superpowers/plans/2026-07-23-cardrush-single-layer-forward.md hikari_bot/plugins/monitors/cardrush.py hikari_bot/plugins/monitors/cardrush_delivery.py hikari_bot/plugins/monitors/cardrush_forward.py tests/cardrush/test_qq_delivery.py tests/cardrush/test_qq_forward.py tests/cardrush/test_plugin_import.py
git commit -m "feat: send Cardrush reports as merged forwards"
git push origin main
```

Expected: `origin/main` points to the new commit and the worktree is clean.
