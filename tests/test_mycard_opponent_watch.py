import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
from nonebot.permission import SUPERUSER

from hikari_bot.services.mycard_archive import opponent_watch as module
from hikari_bot.services.mycard_archive.opponent_watch import OpponentWatch, WatchedAccounts


def room(room_id="one", players=("关注玩家", "对手")):
    return {"id": room_id, "users": [{"username": name} for name in players]}


def test_accounts_persist_duplicate_delete_and_failed_write(tmp_path, monkeypatch):
    path = tmp_path / "watched-accounts.json"
    accounts = WatchedAccounts(path)
    assert accounts.change(" 玩家 A ", add=True)
    assert not accounts.change("玩家 A", add=True)
    assert WatchedAccounts(path).accounts == {"玩家 A"}
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk error")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError):
            accounts.change("玩家 B", add=True)
    assert path.read_bytes() == before
    assert accounts.accounts == {"玩家 A"}
    assert not path.with_suffix(".json.tmp").exists()
    assert accounts.change("玩家 A", add=False)
    assert not accounts.change("玩家 A", add=False)
    assert WatchedAccounts(path).accounts == set()


@pytest.mark.parametrize("raw", ['{}', '[123]', '[""]', 'broken'])
def test_bad_account_file_is_never_overwritten(tmp_path, raw):
    path = tmp_path / "watched-accounts.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        WatchedAccounts(path)
    assert path.read_text(encoding="utf-8") == raw


def setup_watch(tmp_path, monkeypatch, *, latest=None, recipients=("1",)):
    accounts = WatchedAccounts(tmp_path / "watched-accounts.json")
    accounts.change("关注玩家", add=True)
    calls, images, messages = [], [], []
    version = object()

    def lookup(name):
        calls.append(name)
        return latest(name) if latest else version

    async def image(name, actual_version, rules):
        images.append((name, actual_version, rules))
        return b"image"

    async def send(recipient, text, data):
        messages.append((recipient, text, data))

    monkeypatch.setattr(module, "player_image", image)
    library = SimpleNamespace(path=tmp_path / "archive.sqlite3", latest=lookup)
    watcher = OpponentWatch(accounts, library, recipients, send)
    return watcher, calls, images, messages


def test_only_new_watched_matches_send_opponent_image_with_reconnect_dedup(tmp_path, monkeypatch):
    async def run():
        watcher, calls, images, messages = setup_watch(tmp_path, monkeypatch)
        watcher.process({"event": "init", "data": [room("existing")]})
        watcher.process({"event": "create", "data": room("existing")})
        watcher.process({"event": "create", "data": room("unwatched", ("路人", "对手"))})
        watcher.process({"event": "create", "data": room()})
        watcher.process({"event": "create", "data": room(players=("对手", "关注玩家"))})
        watcher.process({"event": "init", "data": [room()]})
        watcher.process({"event": "create", "data": room()})
        watcher.process({"event": "update", "data": room("ignored")})
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert calls == ["对手"]
        assert images[0][0] == "对手"
        assert images[0][2] == tmp_path / "deck-rules.json"
        assert len(messages) == 2
        assert messages[0][2] is None and "https://ygo.xyk.one/mc/" in messages[0][1]
        assert messages[1] == ("1", "关注玩家 vs 对手\n对手：最新已收录卡组", b"image")
        # 同一组玩家再次开局也应通知，不能按用户名永久去重。
        watcher.process({"event": "create", "data": room("next")})
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert len(messages) == 4
        await watcher.close()
    asyncio.run(run())


def test_missing_deck_is_text_only_and_both_watched_players_get_opponents(tmp_path, monkeypatch):
    async def run():
        watcher, calls, images, messages = setup_watch(tmp_path, monkeypatch, latest=lambda _: None)
        watcher.accounts.change("对手", add=True)
        watcher.process({"event": "create", "data": room()})
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert calls == ["对手", "关注玩家"]
        assert images == []
        assert messages == [("1", "关注玩家 vs 对手\n对手：暂无卡组", None),
                            ("1", "对手 vs 关注玩家\n关注玩家：暂无卡组", None)]
        await watcher.close()
    asyncio.run(run())


def test_remove_during_render_cancels_delivery_and_shutdown_cancels_worker(tmp_path, monkeypatch):
    async def run():
        watcher, _, _, messages = setup_watch(tmp_path, monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def image(*args):
            started.set()
            await release.wait()
            return b"image"

        monkeypatch.setattr(module, "player_image", image)
        watcher.process({"event": "create", "data": room()})
        await started.wait()
        watcher.accounts.change("关注玩家", add=False)
        release.set()
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert len(messages) == 1 and messages[0][2] is None
        watcher.accounts.change("关注玩家", add=True)
        release.clear()
        started.clear()
        watcher.process({"event": "create", "data": room("next")})
        watcher.process({"event": "create", "data": room("queued")})
        await started.wait()
        await watcher.close()
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert watcher._worker.done() and all(m[2] is None for m in messages)
    asyncio.run(run())


def test_lookup_failure_and_one_recipient_failure_do_not_stop_later_messages(tmp_path, monkeypatch):
    async def run():
        def broken(name):
            raise OSError("unavailable")

        watcher, _, _, messages = setup_watch(tmp_path, monkeypatch, latest=broken, recipients=("1", "2"))
        async def send(recipient, text, image):
            if recipient == "1":
                raise RuntimeError("QQ unavailable")
            messages.append((recipient, text, image))
        watcher.send = send
        watcher.process({"event": "create", "data": room()})
        watcher.process({"event": "create", "data": room("next")})
        await watcher._queue.join()
        await watcher._image_queue.join()
        assert len(messages) == 2
        assert all(m == ("2", "关注玩家 vs 对手\n对手：卡组暂时无法读取", None) for m in messages)
        await watcher.close()
    asyncio.run(run())


def test_event_processing_does_not_wait_for_render_and_bounds_pending_jobs(tmp_path, monkeypatch):
    async def run():
        watcher, _, _, messages = setup_watch(tmp_path, monkeypatch)
        for index in range(140):
            watcher.process({"event": "create", "data": room(str(index))})
        assert watcher._queue.qsize() == 128
        assert messages == []
        await watcher.close()
    asyncio.run(run())


class Finished(Exception):
    pass


def test_link_precedes_render_and_slow_image_does_not_block_next_link(tmp_path, monkeypatch):
    async def run():
        watcher, _, _, messages = setup_watch(tmp_path, monkeypatch)
        started, release = asyncio.Event(), asyncio.Event()

        async def image(*args):
            assert messages and messages[0][2] is None
            started.set()
            await release.wait()
            return b"image"

        monkeypatch.setattr(module, "player_image", image)
        watcher.process({"event": "create", "data": room()})
        await asyncio.wait_for(started.wait(), 1)
        watcher.process({"event": "create", "data": room("next")})
        await asyncio.wait_for(watcher._queue.join(), 1)
        assert len(messages) == 2
        assert all(data is None and "https://ygo.xyk.one/mc/" in text for _, text, data in messages)
        release.set()
        await asyncio.wait_for(watcher._image_queue.join(), 1)
        assert len(messages) == 4
        await watcher.close()

    asyncio.run(run())


def load_plugin(monkeypatch, tmp_path):
    import nonebot
    import hikari_bot.core.commands

    class Matcher:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.message = None
        def handle(self):
            return lambda fn: fn
        async def finish(self, message):
            self.message = message
            raise Finished()

    monkeypatch.setattr(hikari_bot.core.commands, "on_cmd", lambda cmd, **kw: Matcher(**kw))
    monkeypatch.setattr(nonebot, "get_driver", lambda: SimpleNamespace(
        on_bot_connect=lambda fn: fn, on_shutdown=lambda fn: fn))
    spec = importlib.util.spec_from_file_location("watch_monitor_test", Path("hikari_bot/plugins/monitors/mycard.py"))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    plugin.settings = SimpleNamespace(mycard_archive_dir=tmp_path, superusers={"123"}, mycard_capture_enabled=False)
    return plugin


def test_private_admin_commands_persist_and_escape_names(tmp_path, monkeypatch):
    plugin = load_plugin(monkeypatch, tmp_path)
    assert plugin.add_account.options["permission"] is SUPERUSER
    assert plugin.delete_account.options["permission"] is SUPERUSER
    assert plugin._add_account.__annotations__["event"] is PrivateMessageEvent
    assert plugin._delete_account.__annotations__["event"] is PrivateMessageEvent

    async def run():
        assert await plugin.change_account("", add=True) == "用法：添加账号 MyCard用户名"
        name = "玩家 A[CQ:at,qq=all]"
        args = Message(MessageSegment.text(name))
        with pytest.raises(Finished):
            await plugin._add_account(SimpleNamespace(), args)
        assert plugin.add_account.message.type == "text"
        assert json.loads((tmp_path / "watched-accounts.json").read_text(encoding="utf-8")) == [name]
        assert await plugin.change_account(name, add=True) == f"账号已在名单中：{name}"
        with pytest.raises(Finished):
            await plugin._delete_account(SimpleNamespace(), args)
        assert WatchedAccounts(tmp_path / "watched-accounts.json").accounts == set()
        assert await plugin.change_account(name, add=False) == f"账号不在名单中：{name}"

    from nonebot.adapters.onebot.v11 import MessageSegment
    asyncio.run(run())


def test_monitor_connection_wires_private_image_and_reconnects_without_reset(tmp_path, monkeypatch):
    plugin = load_plugin(monkeypatch, tmp_path)
    messages = []

    async def send(**kwargs):
        messages.append(kwargs)

    async def runner(bot):
        await asyncio.Event().wait()

    async def ignore(*args):
        pass

    monkeypatch.setattr(plugin, "ws_runner", runner)
    monkeypatch.setattr(plugin, "log_message", ignore)
    monkeypatch.setattr(plugin, "handle_create_event", ignore)

    async def run():
        await plugin._on_bot_connect(SimpleNamespace(send_private_msg=send))
        watcher = plugin._opponent_watch
        await watcher.send("123", "a[CQ:at,qq=all]", b"png")
        assert messages[0]["user_id"] == 123
        assert [segment.type for segment in messages[0]["message"]] == ["text", "image"]
        assert messages[0]["message"][1].data["file"].startswith("base64://")
        await plugin.process_mycard_event(None, {"event": "create", "data": room()})
        assert watcher._seen
        await plugin._on_bot_connect(SimpleNamespace(send_private_msg=send))
        assert plugin._opponent_watch is watcher
        await plugin._on_shutdown()
        assert plugin._opponent_watch is None and plugin._ws_task is None
    asyncio.run(run())
