"""房间生命周期与私聊投递；重连去重、限速入场、持久化失败重试。"""
import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from nonebot import logger

from .admission import JoinGate
from .spectator import CredentialError, Credentials, watch
from .store import ArchiveStore
from .scheduling import PrioritySlots


@dataclass
class RoomTask:
    match_id: str
    players: list[str]
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    connected: bool = False
    task: asyncio.Task | None = None
    valid_players: list[str] = field(default_factory=list)
    first_seen: float = 0
    interrupted: asyncio.Event = field(default_factory=asyncio.Event)


class ArchiveRuntime:
    def __init__(self, root: Path, credentials: Path, recipients, send_message,
                 *, concurrency=9, join_interval=0.5, watch_room=watch, rank_filter=None):
        self.store = ArchiveStore(root, recipients)
        self.credentials = Credentials(credentials)
        self.send_message = send_message
        self.watch_room = watch_room
        self.rank_filter = rank_filter
        self.player_ranks = {}
        self.rooms: dict[str, RoomTask] = {}
        self.slots = PrioritySlots(concurrency, self.priorities, lambda state: state.interrupted.set())
        self.semaphore = self.slots.semaphore
        self.concurrency = concurrency
        self.join_gate = JoinGate(interval=join_interval)
        self.delivery_task = None
        self.last_error = ""
        self.initialized = False

    def start(self):
        if self.store.recipients:
            self.delivery_task = asyncio.create_task(self.deliver(), name="mycard-archive-delivery")

    async def process(self, payload: dict):
        event, data = payload.get("event"), payload.get("data")
        if event == "init" and isinstance(data, list):
            present = {r.get("id") for r in data if isinstance(r, dict)}
            for room_id, state in list(self.rooms.items()):
                if room_id not in present:
                    state.ended.set()
            if not self.initialized:
                # 重启时先恢复已落盘文件；不再存在的房间明确标记为中断归档。
                for previous in await asyncio.to_thread(self.store.active):
                    await asyncio.to_thread(self.store.recover_files, previous["id"])
                    if previous["room_id"] not in present:
                        await asyncio.to_thread(self.store.issue, previous["id"], "重启后房间已结束，无法确认录像完整性")
                        await asyncio.to_thread(self.store.finalize, previous["id"], False)
                self.initialized = True
            for room in data:
                await self.add(room)
        elif event in ("create", "update") and isinstance(data, dict):
            await self.add(data)
        elif event == "delete" and isinstance(data, str):
            if data in self.rooms:
                self.rooms[data].ended.set()

    async def add(self, room: dict):
        room_id = room.get("id")
        players = [u.get("username") for u in room.get("users", []) if isinstance(u.get("username"), str)]
        if not isinstance(room_id, str) or not room_id or not players or room_id in self.rooms:
            return
        match_id, active = await asyncio.to_thread(self.store.begin, room_id, players, revive_excluded=True)
        if not active:
            return
        state = RoomTask(match_id, players)
        state.first_seen = await asyncio.to_thread(self.store.first_seen, match_id)
        self.rooms[room_id] = state
        options = room.get("options") or {}
        unavailable = bool(options.get("no_watch") or len(players) not in (2, 4))
        state.task = asyncio.create_task(self.select(room_id, state, unavailable), name=f"mycard-capture-{match_id}")

    async def select(self, room_id, state, unavailable):
        try:
            state.valid_players = list(dict.fromkeys(state.players))
            if unavailable:
                await asyncio.to_thread(self.store.issue, state.match_id, "房间禁止观战或玩家数量不受支持")
                await self.finish_unavailable(room_id, state)
            else:
                # 身份检查在排队之前进行，自己的对局直接跳过，不占任何观战名额。
                while not state.ended.is_set():
                    try:
                        identity = await self.credentials.get()
                        if await self.skip_own_match(state, identity):
                            return
                        break
                    except CredentialError:
                        self.last_error = 'MyCard 认证失败，等待刷新凭据'
                        try:
                            await asyncio.wait_for(state.ended.wait(), 30)
                        except asyncio.TimeoutError:
                            pass
                await self.collect(room_id, state)
        finally:
            self.rooms.pop(room_id, None)

    async def finish_unavailable(self, room_id, state):
        try:
            await state.ended.wait()
            await asyncio.to_thread(self.store.finalize, state.match_id, False)
        finally:
            self.rooms.pop(room_id, None)

    async def skip_own_match(self, state, identity):
        if identity[0] not in state.players:
            return False
        await asyncio.to_thread(self.store.issue, state.match_id, '观战账号正在参与本场对局，已跳过')
        await asyncio.to_thread(self.store.skip, state.match_id)
        return True

    async def priorities(self, states):
        players = {player for state in states if state for player in state.players}
        if self.rank_filter:
            names = sorted(players)
            results = await asyncio.gather(*(self.rank_filter.rank(player) for player in names),
                                           return_exceptions=True)
            for player, rank in zip(names, results):
                # 查询失败保留此前名次；尚未确认时按普通玩家评分。
                if type(rank) is int and rank > 0:
                    self.player_ranks[player] = rank
        latest = await asyncio.to_thread(self.store.library.latest_many, list(players))
        now = time.time()
        keys = []
        for state in states:
            weight = 0
            for player in dict.fromkeys(state.players if state else []):
                version = latest.get(player)
                if version is None:
                    score = 2
                else:
                    stale = now - version.updated_at > 48 * 60 * 60
                    score = int(len(version.deck.side) < 15) + int(stale)
                rank = self.player_ranks.get(player)
                weight += score * (2 if rank is not None and rank <= 100 else 1)
            keys.append((-weight, state.first_seen if state else 0))
        return keys

    def slot(self, ended, state=None):
        return self.slots.slot(ended, state)

    async def capture(self, room_id, state):
        backoff = 5
        while not state.ended.is_set():
            try:
                identity = await self.credentials.get()
                if await self.skip_own_match(state, identity):
                    return None

                async def save(data):
                    # 抢占或关机必须等本次落盘完成，不能在后台仍写文件时释放名额。
                    writing = asyncio.create_task(asyncio.to_thread(self.store.save_replay, state.match_id, data))
                    try:
                        await asyncio.shield(writing)
                    except asyncio.CancelledError:
                        await writing
                        raise

                async with self.join_gate.attempt(state.ended) as admitted:
                    if admitted is None:
                        if self.join_gate.failures >= 3:
                            await asyncio.to_thread(self.store.issue, state.match_id, '入场冷却期间对局结束')
                        break

                    def connected():
                        state.connected = True
                        admitted()

                    complete = await self.watch_room(room_id, identity, state.ended, save, connected=connected)
                if complete or state.ended.is_set():
                    return complete
                await asyncio.to_thread(self.store.issue, state.match_id, '观战连接中断，已尝试重连；完整性以实际收到录像为准')
            except CredentialError:
                self.credentials.invalidate()
                self.last_error = 'MyCard 认证或观战被拒绝，请检查凭据与服务端限制'
                await asyncio.to_thread(self.store.issue, state.match_id, self.last_error)
            except Exception as exc:
                self.last_error = f'观战异常：{type(exc).__name__}'
                logger.warning(f'[mycard_archive] {self.last_error}')
                await asyncio.to_thread(self.store.issue, state.match_id, self.last_error)
            finally:
                state.connected = False
            try:
                await asyncio.wait_for(state.ended.wait(), backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 120)
        return False

    async def capture_until_preempted(self, room_id, state):
        capture = asyncio.create_task(self.capture(room_id, state))
        interrupted = asyncio.create_task(state.interrupted.wait())
        try:
            await asyncio.wait((capture, interrupted), return_when=asyncio.FIRST_COMPLETED)
            if capture.done():
                return False, await capture
            capture.cancel()
            # watch 的 finally 会关闭 socket；完成关闭后才允许归还名额。
            await asyncio.shield(asyncio.gather(capture, return_exceptions=True))
            return True, False
        finally:
            for task in (capture, interrupted):
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(capture, interrupted, return_exceptions=True)

    async def collect(self, room_id: str, state: RoomTask):
        try:
            while True:
                state.interrupted.clear()
                async with self.slot(state.ended, state) as available:
                    complete = False
                    if available:
                        preempted, complete = await self.capture_until_preempted(room_id, state)
                        if complete is None:
                            return  # 账号参与本场，已标为主动跳过。
                        if preempted and not state.ended.is_set():
                            logger.info('[mycard_archive] 低权重对局已关闭连接，重新排队')
                            continue
                    else:
                        await asyncio.to_thread(self.store.issue, state.match_id, '对局在等待观战名额期间结束，未能入场')
                    # 正常结束或排队时结束才归档；抢占不伪造本场结束。
                    if not complete:
                        await asyncio.to_thread(self.store.issue, state.match_id, '未确认正常结束信号，归档可能缺少小局')
                    await asyncio.to_thread(self.store.finalize, state.match_id, complete)
                    return
        except asyncio.CancelledError:
            # 保留 active 和原始文件，下次 init 恢复。
            raise
        except Exception as exc:
            self.last_error = f'归档失败：{type(exc).__name__}'
            logger.error(f'[mycard_archive] {self.last_error}')
        finally:
            self.rooms.pop(room_id, None)

    async def deliver_once(self) -> int:
        # 关闭通知时也不补发历史待投递消息，保留原记录。
        if not self.store.recipients:
            return 0
        pending = await asyncio.to_thread(self.store.pending)
        for row in pending:
            try:
                await self.send_message(row["recipient"], row["message"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"管理员私聊发送失败：{type(exc).__name__}，已排队重试"
                logger.warning(f"[mycard_archive] {self.last_error}")
                await asyncio.to_thread(self.store.delivered, row["id"], False)
            else:
                await asyncio.to_thread(self.store.delivered, row["id"], True)
            await asyncio.sleep(0.5)
        return len(pending)

    async def deliver(self):
        while True:
            try:
                count = await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"发件箱异常：{type(exc).__name__}"
                logger.error(f"[mycard_archive] {self.last_error}")
                count = 0
            await asyncio.sleep(1 if count else 5)

    async def status(self) -> str:
        stats = await asyncio.to_thread(self.store.stats)
        connected = sum(s.connected for s in self.rooms.values())
        return (f"MyCard 采集：跟踪 {len(self.rooms)} 场，已连接 {connected} 场\n"
                f"调度：按采集权重优先，零权重空闲采集（上限 {self.concurrency} 场）\n"
                f"完整结束 {stats.get('complete', 0)} 场，不完整 {stats.get('partial', 0)} 场\n"
                f"待投递 {stats['pending_messages']} 条\n"
                f"入场冷却：{int(self.join_gate.remaining)} 秒\n"
                f"最近异常：{self.last_error or '无'}")

    async def close(self):
        tasks = [s.task for s in self.rooms.values() if s.task]
        if self.delivery_task:
            tasks.append(self.delivery_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.slots.close()
        self.rooms.clear()
