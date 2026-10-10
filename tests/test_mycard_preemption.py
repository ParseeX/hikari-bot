import asyncio
import threading
from types import SimpleNamespace

from hikari_bot.services.mycard_archive.ranking import RankFilter
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime, RoomTask
from hikari_bot.services.mycard_archive.scheduling import PrioritySlots
from hikari_bot.services.mycard_archive.store import ArchiveStore
from test_mycard_archive import replay
from test_mycard_scheduling import ignore, until


def test_equal_weights_sort_by_first_seen_instead_of_partial_side_count(tmp_path):
    async def run():
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1)
        runtime.store.library.latest_many=lambda names: {
            name:SimpleNamespace(deck=SimpleNamespace(side=[1]*(14 if name=='old' else 1))) for name in names}
        states=[RoomTask('new',['new'],valid_players=['new'],first_seen=200),
                RoomTask('old',['old'],valid_players=['old'],first_seen=100)]
        assert await runtime.priorities(states)==[(-0.5,200),(-0.5,100)]
        await runtime.semaphore.acquire()
        order=[]
        async def enter(state):
            async with runtime.slot(state.ended,state) as granted:
                assert granted
                order.append(state.match_id)
        tasks=[asyncio.create_task(enter(state)) for state in states]
        await until(lambda:len(runtime.slots.waiting)==2)
        runtime.semaphore.release()
        await asyncio.wait_for(asyncio.gather(*tasks),3)
        assert order==['old','new']
        await runtime.close()
    asyncio.run(run())


def test_live_excluded_room_resumes_without_changing_first_seen_or_reopening_finished(tmp_path):
    store=ArchiveStore(tmp_path,set())
    mid,_=store.begin('live',['a','b'])
    first=store.first_seen(mid)
    store.exclude(mid)
    restarted=ArchiveStore(tmp_path,set())
    assert restarted.begin('live',['a','b'],revive_excluded=True)==(mid,True)
    assert restarted.first_seen(mid)==first
    store.finalize(mid,False)
    assert restarted.begin('live',['a','b'],revive_excluded=True)==(mid,False)


def test_higher_weight_waits_for_socket_close_then_low_match_resumes_with_replay(tmp_path):
    async def run():
        closing=asyncio.Event()
        close_allowed=asyncio.Event()
        resumed=asyncio.Event()
        calls=[]
        live=peak=0
        async def watcher(room,identity,ended,save,**kwargs):
            nonlocal live,peak
            live+=1
            peak=max(peak,live)
            calls.append(room)
            kwargs['connected']()
            try:
                if room=='low' and calls.count('low')==1:
                    await save(replay())
                    try:
                        await asyncio.Event().wait()
                    finally:
                        closing.set()
                        await close_allowed.wait()
                elif room=='low':
                    await save(replay())  # 重连重复收到同一录像仍应去重。
                    resumed.set()
                    await ended.wait()
                return True
            finally:
                live-=1
        async def rank(name): return 501 if name in ('甲','乙') else 1
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1,
                               watch_room=watcher,join_interval=0,rank_filter=RankFilter(fetch_rank=rank))
        async def identity(): return '观战',42
        runtime.credentials.get=identity
        try:
            await runtime.add({'id':'low','users':[{'username':'甲'},{'username':'乙'}]})
            low=runtime.rooms['low']
            await until(lambda:len(runtime.store.replays(low.match_id))==1)
            original_seen=low.first_seen
            await runtime.add({'id':'high','users':[{'username':'丙'},{'username':'丁'}]})
            await asyncio.wait_for(closing.wait(),3)
            assert calls==['low'] and runtime.semaphore.locked()
            assert runtime.store.stats()['active']==2
            close_allowed.set()
            await asyncio.wait_for(resumed.wait(),3)
            assert calls==['low','high','low'] and peak==1
            assert low.first_seen==original_seen
            assert len(runtime.store.replays(low.match_id))==1
            await runtime.process({'event':'delete','data':'low'})
            await asyncio.wait_for(low.task,3)
            assert runtime.store.library.latest('甲') is not None
            assert not runtime.slots.active and not runtime.semaphore.locked()
        finally:
            close_allowed.set()
            await runtime.close()
    asyncio.run(run())


def test_equal_weight_older_waiter_does_not_preempt_and_weights_can_refresh():
    async def run():
        interrupt=asyncio.Event()
        admitted=asyncio.Event()
        release=asyncio.Event()
        weights={'active':1,'waiting':1}
        async def keys(states):
            return [(-weights[name],100 if name=='waiting' else 200) for name in states]
        slots=PrioritySlots(1,keys,lambda state:interrupt.set(),refresh_interval=0.01)
        async def active():
            async with slots.slot(asyncio.Event(),'active'):
                admitted.set()
                await release.wait()
        first=asyncio.create_task(active())
        await admitted.wait()
        async def waiting():
            async with slots.slot(asyncio.Event(),'waiting') as granted:
                assert granted
        second=asyncio.create_task(waiting())
        await until(lambda:len(slots.waiting)==1)
        await asyncio.sleep(0.04)
        assert not interrupt.is_set()
        weights['waiting']=2
        await asyncio.wait_for(interrupt.wait(),2)
        assert not second.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(first,second),3)
        await slots.close()
    asyncio.run(run())


def test_preemption_chooses_lowest_weight_then_newest_active_room():
    async def run():
        entered=[]
        release={name:asyncio.Event() for name in ('old-low','new-low','high')}
        interrupted=[]
        keys={'old-low':(0,100),'new-low':(0,200),'high':(-2,300)}
        async def priorities(states): return [keys[state] for state in states]
        slots=PrioritySlots(2,priorities,lambda state:interrupted.append(state))
        async def enter(name):
            async with slots.slot(asyncio.Event(),name):
                entered.append(name)
                await release[name].wait()
        tasks=[asyncio.create_task(enter(name)) for name in ('old-low','new-low')]
        await until(lambda:len(entered)==2)
        tasks.append(asyncio.create_task(enter('high')))
        await until(lambda:bool(interrupted))
        assert interrupted==['new-low'] and len(entered)==2
        release['new-low'].set()
        await until(lambda:'high' in entered)
        for event in release.values(): event.set()
        await asyncio.wait_for(asyncio.gather(*tasks),3)
        await slots.close()
    asyncio.run(run())


def test_default_capture_capacity_is_nine(tmp_path):
    async def run():
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore)
        for _ in range(9): await runtime.semaphore.acquire()
        assert runtime.semaphore.locked() and runtime.concurrency==9
        for _ in range(9): runtime.semaphore.release()
        await runtime.close()
    asyncio.run(run())


def test_preemption_waits_for_inflight_replay_write_before_releasing_slot(tmp_path):
    async def run():
        writing=threading.Event()
        finish_write=threading.Event()
        closed=asyncio.Event()
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1,join_interval=0)
        original=runtime.store.save_replay
        def save(mid,data):
            writing.set()
            assert finish_write.wait(3)
            original(mid,data)
        runtime.store.save_replay=save
        async def watch(room,identity,ended,save,**kwargs):
            kwargs['connected']()
            try:
                await save(replay())
                await asyncio.Event().wait()
            finally:
                closed.set()
        runtime.watch_room=watch
        async def identity(): return '观战',42
        runtime.credentials.get=identity
        mid,_=runtime.store.begin('saving',['甲','乙'])
        state=RoomTask(mid,['甲','乙'],valid_players=['甲','乙'])
        task=asyncio.create_task(runtime.collect('saving',state))
        try:
            await until(writing.is_set)
            state.interrupted.set()
            await asyncio.sleep(0.02)
            assert runtime.semaphore.locked() and not closed.is_set()
            # 抢占后本场也已结束：落盘必须完成，随后以不完整归档入库。
            state.ended.set()
            finish_write.set()
            await asyncio.wait_for(task,3)
            assert closed.is_set() and not runtime.semaphore.locked()
            assert len(runtime.store.replays(mid))==1
            assert runtime.store.library.latest('甲') is not None
        finally:
            finish_write.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            await runtime.close()
    asyncio.run(run())
