# Cardrush QQ Timeout Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Cardrush QQ 图报在单页返回 `retcode=1200` 时静默继续后续发送，同时保留其他异常的原有失败行为。

**Architecture:** 在无 NoneBot 依赖的 `cardrush_delivery.py` 中集中实现逐页容错发送，由手动命令和自动任务注入各自的单页发送回调。容错仅基于异常的数值型 `retcode`，不会重试结果未知的页面。

**Tech Stack:** Python 3.10+、asyncio、NoneBot OneBot V11、pytest。

## Global Constraints

- 直接在 `main` 分支修改、提交并推送。
- 仅忽略 `retcode == 1200`，其他异常必须原样抛出。
- `1200` 页面不得重试，以免实际已送达时重复发送。
- 手动与自动图报必须继续发送后续页面。
- 自动图报遇到可容忍超时后仍须继续 B 站发布。
- 不改变现有 200KB WebP 压缩策略。

---

### Task 1: Add and integrate page-level QQ timeout tolerance

**Files:**
- Modify: `tests/cardrush/test_qq_delivery.py`
- Modify: `tests/cardrush/test_plugin_import.py`
- Modify: `hikari_bot/plugins/monitors/cardrush_delivery.py`
- Modify: `hikari_bot/plugins/monitors/cardrush.py`

**Interfaces:**
- Consumes: `Sequence[bytes]` pages and an async `Callable[[bytes], Awaitable[None]]`.
- Produces: `send_qq_pages(pages, send_page, *, log_prefix) -> list[int]`, returning one-based page indexes that reported `retcode=1200`.

- [ ] **Step 1: Write failing continuation and propagation tests**

Add the following behavior tests to `test_qq_delivery.py`:

```python
class SendError(Exception):
    def __init__(self, retcode):
        super().__init__(f"retcode={retcode}")
        self.retcode = retcode


def test_send_qq_pages_continues_after_retcode_1200(monkeypatch):
    attempts = []
    logs = []

    async def fake_send(page):
        attempts.append(page)
        if page == b"one":
            raise SendError(1200)

    async def fake_log(message):
        logs.append(message)

    monkeypatch.setattr(delivery, "log_message", fake_log)

    timeouts = asyncio.run(
        delivery.send_qq_pages(
            [b"one", b"two", b"three"],
            fake_send,
            log_prefix="[test]",
        )
    )

    assert attempts == [b"one", b"two", b"three"]
    assert timeouts == [1]
    assert "page 1/3" in logs[0]
    assert "retcode=1200" in logs[0]


def test_send_qq_pages_reraises_other_errors():
    attempts = []
    expected = SendError(100)

    async def fake_send(page):
        attempts.append(page)
        if page == b"two":
            raise expected

    with pytest.raises(SendError) as caught:
        asyncio.run(
            delivery.send_qq_pages(
                [b"one", b"two", b"three"],
                fake_send,
                log_prefix="[test]",
            )
        )

    assert caught.value is expected
    assert attempts == [b"one", b"two"]
```

Import `pytest`, then replace the two inline-loop assertions in
`test_plugin_import.py` with:

```python
assert source.count("await send_qq_pages(") == 2
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
uv run pytest tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
```

Expected: failure because `send_qq_pages` does not exist and the two handlers still contain inline loops.

- [ ] **Step 3: Implement the minimal shared sender**

In `cardrush_delivery.py`, import `Awaitable` and `Callable`, then add:

```python
async def send_qq_pages(
    pages: Sequence[bytes],
    send_page: Callable[[bytes], Awaitable[None]],
    *,
    log_prefix: str,
) -> list[int]:
    timed_out_pages: list[int] = []
    total = len(pages)
    for index, page in enumerate(pages, 1):
        try:
            await send_page(page)
        except Exception as error:
            if getattr(error, "retcode", None) != 1200:
                raise
            timed_out_pages.append(index)
            await log_message(
                f"{log_prefix} QQ send page {index}/{total}: "
                "retcode=1200 ignored; continuing."
            )
    return timed_out_pages
```

- [ ] **Step 4: Integrate both delivery paths**

Import `send_qq_pages` in `cardrush.py`. Replace the manual inline loop with:

```python
async def send_page(page: bytes) -> None:
    encoded = base64.b64encode(page).decode()
    await bot.send(
        event,
        MessageSegment.image(f"base64://{encoded}"),
    )

await send_qq_pages(
    qq_pages,
    send_page,
    log_prefix="[cardrush]",
)
```

Replace the automatic nested page loop with:

```python
for user_id in ADMIN:
    async def send_page(
        screenshot: bytes,
        recipient: str = user_id,
    ) -> None:
        encoded = base64.b64encode(screenshot).decode()
        await bot.send_private_msg(
            user_id=int(recipient),
            message=MessageSegment.image(
                f"base64://{encoded}"
            ),
        )

    await send_qq_pages(
        qq_screenshots,
        send_page,
        log_prefix=f"[cardrush_auto] user={user_id}",
    )
```

- [ ] **Step 5: Run focused tests to verify GREEN**

Run:

```powershell
uv run pytest tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Run repository verification**

Run:

```powershell
uv run pytest -q
uv run python -m compileall -q bot.py hikari_bot scripts
uv run python -m pyflakes bot.py hikari_bot scripts
uv run nb-cli plugin load hikari_bot.plugins.monitors.cardrush
```

Expected: all commands exit with code 0.

- [ ] **Step 7: Commit and push**

Run:

```powershell
git add docs/superpowers/specs/2026-07-23-cardrush-qq-timeout-continuation-design.md docs/superpowers/plans/2026-07-23-cardrush-qq-timeout-continuation.md tests/cardrush/test_qq_delivery.py tests/cardrush/test_plugin_import.py hikari_bot/plugins/monitors/cardrush_delivery.py hikari_bot/plugins/monitors/cardrush.py
git commit -m "fix: tolerate Cardrush QQ send timeouts"
git push origin main
```

Expected: commit is created on `main` and `origin/main` points to it.
