import asyncio
from types import SimpleNamespace

import pytest

from hikari_bot.services.mycard_archive.ranking import RankFilter
from hikari_bot.services.mycard_archive.runtime import ArchiveRuntime, RoomTask
from hikari_bot.services.mycard_archive.scheduling import PrioritySlots


async def ignore(*args):
    pass


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.parametrize('rank,multiplier', [(1,2),(100,2),(101,1),(500,1),(501,1)])
@pytest.mark.parametrize('side,age,score', [(None,0,2),(0,172801,2),(14,172800,1),
                                          (14,172799,1),(15,172801,1),(15,172800,0)])
def test_priority_score_matrix(tmp_path, monkeypatch, rank, multiplier, side, age, score):
    async def run():
        async def fetch(name): return rank
        runtime = ArchiveRuntime(tmp_path, tmp_path/'creds', set(), ignore,
                                 rank_filter=RankFilter(fetch_rank=fetch))
        monkeypatch.setattr('hikari_bot.services.mycard_archive.runtime.time.time', lambda: 1000000)
        runtime.store.library.latest_many = lambda names: {} if side is None else {
            name: SimpleNamespace(deck=SimpleNamespace(side=[1]*side), updated_at=1000000-age)
            for name in names}
        states = [RoomTask('pair', ['a','b'], first_seen=123)]
        assert await runtime.priorities(states) == [(-2*score*multiplier,123)]
    asyncio.run(run())


def test_rank_failure_retains_known_rank_and_defaults_other_players(tmp_path):
    async def run():
        async def fetch(name): raise ValueError('unavailable')
        runtime = ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,
                                 rank_filter=RankFilter(fetch_rank=fetch))
        runtime.player_ranks['known'] = 100
        state = RoomTask('pair',['known','unknown'])
        assert await runtime.priorities([state]) == [(-6,0)]
    asyncio.run(run())


def test_rank_checks_both_players_and_500_boundary():
    async def run():
        calls=[]
        async def fetch(name):
            calls.append(name)
            return {'a':1,'b':500,'c':501}[name]
        ranks=RankFilter(fetch_rank=fetch)
        assert await ranks.valid_players(['a','b']) == ['a','b']
        assert calls == ['a','b']
        assert await ranks.valid_players(['b','c']) == ['b']
        assert await ranks.valid_players(['c']) == []
        assert await ranks.valid_players(['unknown','a']) == ['a']
        with pytest.raises(ValueError):
            await ranks.valid_players(['unknown','c'])
    asyncio.run(run())


def test_queue_reorders_at_release_using_latest_decks_and_keeps_fifo_ties(tmp_path):
    async def run():
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1)
        sides={'a':0,'b':7,'c':7}
        def latest(names):
            return {n:SimpleNamespace(deck=SimpleNamespace(side=[1]*sides[n]), updated_at=__import__('time').time()) for n in names}
        runtime.store.library.latest_many=latest
        await runtime.semaphore.acquire()
        order=[]
        async def enter(name):
            state=RoomTask(name,[name],valid_players=[name])
            async with runtime.slot(state.ended,state) as granted:
                assert granted
                order.append(name)
        tasks=[]
        for name in ['a','b','c']:
            tasks.append(asyncio.create_task(enter(name)))
            await until(lambda:len(runtime.slots.waiting)==len(tasks))
        sides['a']=15
        runtime.semaphore.release()
        await asyncio.wait_for(asyncio.gather(*tasks),3)
        assert order==['b','c','a']
        assert not runtime.semaphore.locked()
        await runtime.close()
    asyncio.run(run())


def test_own_match_is_skipped_before_queue_even_when_all_slots_busy(tmp_path):
    async def run():
        async def forbidden(*args,**kwargs):
            pytest.fail('自己的对局不应尝试观战')
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1,watch_room=forbidden)
        async def identity(): return '自己',42
        runtime.credentials.get=identity
        await runtime.semaphore.acquire()
        await runtime.add({'id':'own','users':[{'username':'自己'},{'username':'对手'}]})
        task=runtime.rooms['own'].task
        await asyncio.wait_for(task,2)
        assert runtime.store.stats()['skipped']==1
        assert not runtime.slots.waiting
        assert runtime.semaphore.locked()
        runtime.semaphore.release()
        async with runtime.slot(asyncio.Event()) as granted:
            assert granted
        await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('cancel', [False, True])
def test_cancelled_or_ended_top_candidate_does_not_block_or_leak_slot(cancel):
    async def run():
        async def priorities(states): return states
        slots=PrioritySlots(1,priorities)
        await slots.semaphore.acquire()
        ended=asyncio.Event()
        results=[]
        async def enter(key,event):
            async with slots.slot(event,key) as granted:
                results.append((key,granted))
        first=asyncio.create_task(enter(0,ended))
        second=asyncio.create_task(enter(1,asyncio.Event()))
        await until(lambda:len(slots.waiting)==2)
        if cancel: first.cancel()
        else: ended.set()
        await asyncio.gather(first,return_exceptions=True)
        slots.semaphore.release()
        await asyncio.wait_for(second,2)
        assert (1,True) in results
        assert (0,True) not in results
        assert not slots.semaphore.locked()
        await slots.close()
    asyncio.run(run())


def test_priority_does_not_preempt_active_room_and_limits_concurrency():
    async def run():
        async def priorities(states): return states
        slots=PrioritySlots(1,priorities)
        release=asyncio.Event()
        order=[]
        async def enter(key):
            async with slots.slot(asyncio.Event(),key) as granted:
                assert granted
                order.append(key)
                if key==10: await release.wait()
        active=asyncio.create_task(enter(10))
        await until(lambda:order==[10])
        low=asyncio.create_task(enter(5))
        high=asyncio.create_task(enter(0))
        await until(lambda:len(slots.waiting)==2)
        assert order==[10]
        release.set()
        await asyncio.wait_for(asyncio.gather(active,low,high),3)
        assert order==[10,0,5]
        await slots.close()
    asyncio.run(run())


def test_finishing_match_is_saved_before_next_priority_lookup(tmp_path):
    async def run():
        release=asyncio.Event()
        calls=[]
        async def watch(room,identity,ended,save,**kwargs):
            kwargs['connected']()
            calls.append(room)
            if room=='active': await release.wait()
            else: assert saved, '新连接必须在前一场归档后开始'
            return True
        runtime=ArchiveRuntime(tmp_path,tmp_path/'creds',set(),ignore,concurrency=1,
                               join_interval=0,watch_room=watch)
        async def identity(): return '观战',42
        runtime.credentials.get=identity
        saved=[]
        original_finalize=runtime.store.finalize
        def finalize(match_id,complete):
            original_finalize(match_id,complete)
            saved.append(match_id)
        runtime.store.finalize=finalize
        async def add(name):
            mid,_=runtime.store.begin(name,[name])
            state=RoomTask(mid,[name],valid_players=[name])
            return asyncio.create_task(runtime.collect(name,state))
        first=await add('active')
        await until(lambda:calls==['active'])
        second=await add('next')
        await until(lambda:len(runtime.slots.waiting)==1)
        release.set()
        await asyncio.wait_for(asyncio.gather(first,second),3)
        assert calls==['active','next'] and len(saved)==2
        await runtime.close()
    asyncio.run(run())


def test_cancellation_during_priority_lookup_releases_reserved_slot():
    async def run():
        lookup=asyncio.Event()
        async def priorities(states):
            lookup.set()
            await asyncio.Event().wait()
        slots=PrioritySlots(1,priorities)
        async def enter():
            async with slots.slot(asyncio.Event(),0):
                pytest.fail('未完成排序不能入场')
        task=asyncio.create_task(enter())
        await asyncio.wait_for(lookup.wait(),2)
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await slots.close()
        assert not slots.semaphore.locked() and not slots.waiting
    asyncio.run(run())
