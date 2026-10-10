"""房间生命周期与私聊投递；重连去重、限速入场、持久化失败重试。"""
import asyncio
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


class ArchiveRuntime:
    def __init__(self, root: Path, credentials: Path, recipients, send_message,
                 *, concurrency=8, join_interval=0.5, watch_room=watch, rank_filter=None):
        self.store = ArchiveStore(root, recipients)
        self.credentials = Credentials(credentials)
        self.send_message = send_message
        self.watch_room = watch_room
        self.rank_filter = rank_filter
        self.rooms: dict[str, RoomTask] = {}
        self.slots = PrioritySlots(concurrency, self.priorities)
        self.semaphore = self.slots.semaphore
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
        match_id, active = await asyncio.to_thread(self.store.begin, room_id, players)
        if not active:
            return
        state = RoomTask(match_id, players)
        self.rooms[room_id] = state
        options = room.get("options") or {}
        unavailable = bool(options.get("no_watch") or len(players) not in (2, 4))
        state.task = asyncio.create_task(self.select(room_id, state, unavailable), name=f"mycard-capture-{match_id}")

    async def select(self, room_id, state, unavailable):
        try:
            if self.rank_filter:
                while True:
                    try:
                        state.valid_players = await self.rank_filter.valid_players(state.players)
                        eligible = bool(state.valid_players)
                        break
                    except Exception:
                        rank_error = "排名查询失败，未确认前 500 的对局暂不入场"
                        self.last_error = rank_error
                        try:
                            await asyncio.wait_for(state.ended.wait(), 30)
                        except asyncio.TimeoutError:
                            continue
                        await asyncio.to_thread(self.store.issue, state.match_id, rank_error)
                        await asyncio.to_thread(self.store.finalize, state.match_id, False)
                        return
                if not eligible:
                    await asyncio.to_thread(self.store.exclude, state.match_id)
                    return
            else:
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
        players = {player for state in states if state for player in state.valid_players}
        latest = await asyncio.to_thread(self.store.library.latest_many, list(players))
        keys = []
        for state in states:
            valid = state.valid_players if state else []
            unknown = sum(player not in latest for player in valid)
            side = sum(len(latest[player].deck.side) for player in valid if player in latest)
            keys.append((-len(valid), -unknown, side))
        return keys

    def slot(self, ended, state=None):
        return self.slots.slot(ended, state)

    async def collect(self, room_id: str, state: RoomTask):
        complete = False
        backoff = 5
        try:
            async with self.slot(state.ended, state) as available:
                if not available:
                    await asyncio.to_thread(self.store.issue, state.match_id, "对局在等待观战名额期间结束，未能入场")
                while available and not state.ended.is_set():
                    try:
                        if self.rank_filter and not await self.rank_filter.eligible(state.players):
                            await asyncio.to_thread(self.store.exclude, state.match_id)
                            return
                        identity = await self.credentials.get()
                        # 本人对局不能以同一账号加入，避免干扰用户正在进行的游戏。
                        if await self.skip_own_match(state, identity):
                            return
                        async def save(data):
                            await asyncio.to_thread(self.store.save_replay, state.match_id, data)

                        async with self.join_gate.attempt(state.ended) as admitted:
                            if admitted is None:
                                if self.join_gate.failures >= 3:
                                    await asyncio.to_thread(self.store.issue, state.match_id, "入场冷却期间对局结束")
                                break

                            def connected():
                                state.connected = True
                                admitted()

                            complete = await self.watch_room(room_id, identity, state.ended, save, connected=connected)
                        if complete or state.ended.is_set():
                            break
                        await asyncio.to_thread(self.store.issue, state.match_id, "观战连接中断，已尝试重连；完整性以实际收到录像为准")
                    except CredentialError:
                        self.credentials.invalidate()
                        self.last_error = "MyCard 认证或观战被拒绝，请检查凭据与服务端限制"
                        await asyncio.to_thread(self.store.issue, state.match_id, self.last_error)
                    except Exception as exc:
                        self.last_error = f"观战异常：{type(exc).__name__}"
                        logger.warning(f"[mycard_archive] {self.last_error}")
                        await asyncio.to_thread(self.store.issue, state.match_id, self.last_error)
                    finally:
                        state.connected = False
                    try:
                        await asyncio.wait_for(state.ended.wait(), backoff)
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(backoff * 2, 120)
                # 先入库再释放名额，使下一次排序能看到刚补齐的卡组和 Side。
                if not complete:
                    await asyncio.to_thread(self.store.issue, state.match_id, "未确认正常结束信号，归档可能缺少小局")
                await asyncio.to_thread(self.store.finalize, state.match_id, complete)
        except asyncio.CancelledError:
            # 保留 active 和原始文件，下次 init 恢复；不伪造对局结束。
            raise
        except Exception as exc:
            self.last_error = f"归档失败：{type(exc).__name__}"
            logger.error(f"[mycard_archive] {self.last_error}")
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
                f"筛选：{'至少一方竞技场前 500（排名缓存 5 分钟）' if self.rank_filter else '所有对局'}\n"
                f"不符合排名范围 {stats.get('excluded', 0)} 场\n"
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
