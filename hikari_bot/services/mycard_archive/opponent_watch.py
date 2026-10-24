"""管理员关注账号开局时，投递对手已收录的最新卡组。"""

import asyncio
from collections import OrderedDict
import json
import os
import time
from pathlib import Path
from threading import Lock

from nonebot import logger

from .library import DeckLibrary
from .queries import player_image
from .short_links import ShortLinks


class WatchedAccounts:
    """单进程内共享名单；先原子保存，再更新内存，写入失败不丢原名单。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        try:
            accounts = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            accounts = []
        if not isinstance(accounts, list) or any(
            not isinstance(name, str) or not name.strip() for name in accounts
        ):
            raise ValueError("关注账号名单格式错误")
        self.accounts = frozenset(accounts)

    def change(self, name: str, *, add: bool) -> bool:
        name = name.strip()
        if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
            raise ValueError("请填写有效的 MyCard 用户名（最多 100 字）")
        with self._lock:
            if (name in self.accounts) == add:
                return False
            accounts = self.accounts | {name} if add else self.accounts - {name}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as output:
                    json.dump(sorted(accounts), output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.accounts = frozenset(accounts)
            return True


class OpponentWatch:
    def __init__(self, accounts: WatchedAccounts, library: DeckLibrary, recipients, send):
        self.accounts = accounts
        self.library = library
        self.recipients = tuple(sorted(recipients))
        self.send = send
        self._seen = OrderedDict()
        self._queue = asyncio.Queue(maxsize=128)
        self._worker = None
        self._image_queue = asyncio.Queue(maxsize=128)
        self._image_worker = None

    def process(self, payload: dict):
        """事件循环只做过滤和排队，不等待查库、绘图或 QQ 发送。"""
        event, data = payload.get("event"), payload.get("data")
        if event not in ("init", "create"):
            return
        rooms = data if event == "init" and isinstance(data, list) else [data]
        for room in rooms:
            if not isinstance(room, dict) or room.get("id") is None:
                continue
            users = room.get("users")
            if not isinstance(users, list):
                continue
            players = [user.get("username") for user in users if isinstance(user, dict)]
            if len(players) != 2 or not all(isinstance(p, str) and p for p in players):
                continue
            key = (str(room["id"]), tuple(sorted(players)))
            if key in self._seen:
                continue
            self._seen[key] = None
            if len(self._seen) > 4096:
                self._seen.popitem(last=False)
            # 初次连接和重连的快照仅用于去重，不补发已在进行的对局。
            if event == "init":
                continue
            for index, player in enumerate(players):
                if player not in self.accounts.accounts:
                    continue
                try:
                    self._queue.put_nowait((player, players[1 - index]))
                except asyncio.QueueFull:
                    logger.warning("[mycard_watch] 开局通知队列已满，跳过本次通知")
            if not self._queue.empty() and (self._worker is None or self._worker.done()):
                self._worker = asyncio.create_task(self._run())

    async def _prepare(self, player: str, opponent: str):
        def lookup():
            version = self.library.latest(opponent)
            link = ShortLinks(self.library.path.with_name("short-links.sqlite3")).url(opponent) if version else None
            return version, link
        version, link = await asyncio.to_thread(lookup)
        heading = f"{player} vs {opponent}"
        if version is None:
            return f"{heading}\n{opponent}：暂无卡组", None
        return f"{heading}\n{opponent}：最新已收录卡组\n{link}", version

    async def _deliver(self, player, text, image, stage):
        for recipient in self.recipients:
            if player not in self.accounts.accounts:
                break
            started = time.monotonic()
            try:
                await asyncio.wait_for(self.send(recipient, text, image), timeout=30)
                logger.info(f"[mycard_watch] {stage}接口返回成功 bytes={len(image) if image else 0} elapsed={time.monotonic()-started:.2f}s")
            except Exception as exc:
                logger.warning(f"[mycard_watch] {stage}发送失败：{type(exc).__name__} bytes={len(image) if image else 0} elapsed={time.monotonic()-started:.2f}s")

    async def _run(self):
        while not self._queue.empty():
            player, opponent = self._queue.get_nowait()
            try:
                if player not in self.accounts.accounts:
                    continue
                try:
                    text, version = await asyncio.wait_for(self._prepare(player, opponent), timeout=20)
                except Exception as exc:
                    logger.warning(f"[mycard_watch] 对手卡组读取失败：{type(exc).__name__}")
                    text, version = f"{player} vs {opponent}\n{opponent}：卡组暂时无法读取", None
                await self._deliver(player, text, None, "链接通知")
                if version is not None and player in self.accounts.accounts:
                    try:
                        self._image_queue.put_nowait((player, opponent, version))
                    except asyncio.QueueFull:
                        logger.warning("[mycard_watch] 图片队列已满，链接通知已尝试发送")
                    if not self._image_queue.empty() and (self._image_worker is None or self._image_worker.done()):
                        self._image_worker = asyncio.create_task(self._run_images())
            finally:
                self._queue.task_done()

    async def _run_images(self):
        while not self._image_queue.empty():
            player, opponent, version = self._image_queue.get_nowait()
            try:
                if player not in self.accounts.accounts:
                    continue
                started = time.monotonic()
                try:
                    image = await asyncio.wait_for(player_image(opponent, version, self.library.path.with_name("deck-rules.json")), timeout=120)
                    logger.info(f"[mycard_watch] 图片生成完成 bytes={len(image)} elapsed={time.monotonic()-started:.2f}s")
                    await self._deliver(player, f"{player} vs {opponent}\n{opponent}：最新已收录卡组", image, "卡组图片")
                except Exception as exc:
                    logger.warning(f"[mycard_watch] 卡组图片生成失败：{type(exc).__name__} elapsed={time.monotonic()-started:.2f}s")
                    await self._deliver(player, f"{player} vs {opponent}\n{opponent}：卡组图片暂时无法生成", None, "图片失败提示")
            finally:
                self._image_queue.task_done()

    async def close(self):
        tasks = [task for task in (self._worker, self._image_worker) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for queue in (self._queue, self._image_queue):
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
