import asyncio
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.admin import AdminQueries
from hikari_bot.qq_official.notifications import Notifications
from hikari_bot.services.official_bus import OfficialBus, encode, decode
from hikari_bot.services.official_control import redact
from hikari_bot.services.platform import Identity, Request, Result
from hikari_bot.services.platform_state import PlatformState


def setup(tmp_path):
    now = [1000.0]
    config = PilotConfig('app', 'synthetic', users=frozenset({'owner','user'}), admins=frozenset({'owner'}))
    bus = OfficialBus(tmp_path, clock=lambda: now[0])
    bus.configure(config)
    owner = Identity('qq_official','app','user','owner')
    user = Identity('qq_official','app','user','user')
    return now, config, bus, owner, user


def test_requires_consent_and_never_maps_same_numeric_identifier(tmp_path):
    _, config, bus, owner, user = setup(tmp_path)
    bus.publish(Result('private'), dedupe='one')
    assert bus.claim('notice',app='app') is None
    bus.allow_topic(owner,'admin',True)
    bus.publish(Result('private'), dedupe='one')
    job = bus.claim('notice',app='app')
    assert job['target']==owner.key
    assert decode(job['body']).text=='private'
    bus.publish(Result('private'), dedupe='one')
    assert bus.claim('notice',app='app') is None
    try:
        bus.allow_topic(user,'admin',True)
        assert False
    except ValueError:
        pass
    bus.configure(PilotConfig('app','synthetic',users=frozenset({'user'})))
    bus.enqueue('notice',owner.key,'admin',encode(Result('denied')),'two')
    assert bus.claim('notice',app='app') is None


def test_crash_does_not_replay_unknown_send_and_limits_are_per_recipient(tmp_path):
    now, config, bus, owner, user = setup(tmp_path)
    bus.allow_topic(owner,'test',True)
    for n in range(2):
        bus.enqueue('notice',owner.key,'test',encode(Result('test')),str(n),lifetime=500)
    first=bus.claim('notice',app='app')
    now[0]+=181
    assert bus.claim('notice',app='other') is None
    assert bus.response(first['id'])[0]=='unknown'
    assert bus.claim('notice',app='app',daily_limit=1) is None
    now[0]+=6
    assert bus.claim('notice',app='app',daily_limit=2) is not None


def test_control_rechecks_admin_after_enqueue_and_expired_jobs_are_not_executed(tmp_path):
    now, config, bus, owner, user = setup(tmp_path)
    body=json.dumps({'action':'status','argument':''})
    denied=bus.enqueue('control',user.key,'control',body,'request')
    assert bus.claim('control') is None
    assert bus.response(denied)[0]=='denied'
    expired=bus.enqueue('control',owner.key,'control',body,'expired',lifetime=1)
    now[0]+=2
    assert bus.claim('control') is None
    assert bus.response(expired)[0]=='expired'
    key=bus.enqueue('control',owner.key,'control',body,'ok')
    assert bus.claim('control')['id']==key
    result=Result('query',images=(b'png',),file=b'zip',filename='test.zip')
    bus.finish(key,'completed',result)
    assert bus.response(key)==('completed',result)


def test_proactive_worker_uses_no_passive_message_id_and_does_not_retry_failure(tmp_path):
    async def run():
        now, config, bus, owner, user=setup(tmp_path)
        bus.allow_topic(owner,'test',True)
        key=bus.enqueue('notice',owner.key,'test',encode(Result('probe')),'probe')
        worker=Notifications(bus,PlatformState(tmp_path),config)
        client=SimpleNamespace(self_id='app',send_to_c2c=AsyncMock(side_effect=TimeoutError()))
        worker.bot=client
        # 窄适配器保持传入调用参数，测试真实 worker 的无 msg_id 投递路径。
        with patch('hikari_bot.qq_official.notifications.NamedFileBot',lambda bot:bot):
            await worker.tick()
            await worker.tick()
        client.send_to_c2c.assert_awaited_once()
        assert 'msg_id' not in client.send_to_c2c.call_args.kwargs
        assert bus.response(key)[0]=='failed'
    asyncio.run(run())


def test_management_is_c2c_admin_only_and_backend_must_be_online(tmp_path):
    async def run():
        _, config, bus, owner, user=setup(tmp_path)
        admin=AdminQueries(config,bus=bus)
        result=await admin.handle(Request(user,user,'e',''),'状态','')
        assert '权限不足' in result.text
        result=await admin.handle(Request(owner,owner,'e',''),'状态','')
        assert '离线' in result.text
        group=Identity('qq_official','app','group','g')
        result=await admin.handle(Request(owner,group,'e',''),'状态','')
        assert '权限不足' in result.text
    asyncio.run(run())


def test_logs_redact_known_credentials_and_url_queries():
    text=redact('token=hidden Authorization: secretpass https://example.com/?private=one exact-value', ['exact-value'])
    assert all(secret not in text for secret in ['hidden','secretpass','private=one','exact-value'])


def test_unsubscribe_cancels_already_queued_player_notice(tmp_path):
    async def run():
        now, config, bus, owner, user=setup(tmp_path)
        state=PlatformState(tmp_path)
        state.subscribe(owner,'player')
        bus.allow_topic(owner,'mycard',True)
        bus.publish(Result('start'),dedupe='room',topic='mycard',subscription='player')
        state.unsubscribe(owner,'player')
        worker=Notifications(bus,state,config,enabled=True)
        worker.bot=SimpleNamespace(self_id='app',send_to_c2c=AsyncMock())
        with patch('hikari_bot.qq_official.notifications.NamedFileBot',lambda bot:bot):
            await worker.tick()
        worker.bot.send_to_c2c.assert_not_awaited()
        assert bus.status()[1]['cancelled']==1
    asyncio.run(run())


def test_rate_limit_pauses_target_instead_of_sending_next_notice(tmp_path):
    async def run():
        from nonebot.adapters.qq.exception import ActionFailed
        from nonebot.drivers import Response
        now, config, bus, owner, user=setup(tmp_path)
        bus.allow_topic(owner,'test',True)
        for n in range(2):
            bus.enqueue('notice',owner.key,'test',encode(Result('probe')),str(n),lifetime=4000)
        worker=Notifications(bus,PlatformState(tmp_path),config)
        worker.bot=SimpleNamespace(self_id='app',send_to_c2c=AsyncMock(side_effect=ActionFailed(Response(429, content='{"code":22009}'))))
        with patch('hikari_bot.qq_official.notifications.NamedFileBot',lambda bot:bot):
            await worker.tick()
            now[0]+=10
            await worker.tick()
        worker.bot.send_to_c2c.assert_awaited_once()
    asyncio.run(run())


def test_official_management_requires_same_app_and_one_shot_owner_confirmation(tmp_path):
    async def run():
        from hikari_bot.qq_official.management import Management
        _, config, bus, owner, user=setup(tmp_path)
        action=AsyncMock()
        manager=Management(config,bus,restart=action)
        other_app=Identity('qq_official','other','user','owner')
        result=await manager.handle(Request(other_app,other_app,'e',''),'重载','')
        assert '权限不足' in result.text
        req=Request(owner,owner,'e','')
        await manager.handle(req,'重载','')
        token=manager.confirmations[owner.key][1]
        with patch.object(manager,'delayed',new=AsyncMock()) as delayed:
            result=await manager.handle(Request(user,user,'e2',''),'确认操作',token)
            assert '权限不足' in result.text
            await manager.handle(req,'确认操作',token)
            await asyncio.sleep(0)
            delayed.assert_awaited_once_with(action)
            assert '无效' in (await manager.handle(req,'确认操作',token)).text
        action.assert_not_awaited()
    asyncio.run(run())


def test_registered_official_handler_binds_replies_and_deduplicates_after_restart(tmp_path):
    script = '''
import asyncio,sys
from datetime import datetime,timezone
from types import SimpleNamespace,MethodType
from unittest.mock import AsyncMock
import nonebot
nonebot.logger.remove()
nonebot.init(driver="~aiohttp",_env_file=[],log_level="ERROR")
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.business import Queries
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.services.platform_state import PlatformState
config=PilotConfig('app','synthetic',users=frozenset({'user'}))
state=PlatformState(sys.argv[1])
client=SimpleNamespace(self_id='app',send_to_c2c=AsyncMock(return_value=SimpleNamespace(id='reply')))
client.send=MethodType(Bot.send,client)
def event(id,text):
 return C2CMessageCreateEvent.model_validate(dict(id=id,content=text,timestamp=datetime.now(timezone.utc).isoformat(),author=dict(id='user',user_openid='user')))
async def run():
 handler=register(config,queries=Queries(None,state=state)).handlers[0].call
 await handler(client,event('first','绑定 synthetic-player'))
 assert client.send_to_c2c.await_count==1
 await handler(client,event('first','绑定 other-player'))
 assert client.send_to_c2c.await_count==1
 restarted=register(config,queries=Queries(None,state=state)).handlers[0].call
 await restarted(client,event('first','绑定 other-player'))
 assert client.send_to_c2c.await_count==1
 await restarted(client,event('second','查询绑定'))
 assert 'synthetic-player' in str(client.send_to_c2c.call_args.kwargs['message'])
asyncio.run(run())
'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0, result.stderr[-3000:]


def test_timed_out_document_download_cannot_start_a_second_thread():
    import threading
    from hikari_bot.services import official_control
    async def run():
        started, release = threading.Event(), threading.Event()
        calls = []
        def download(*args):
            calls.append(args)
            started.set()
            release.wait(3)
        try:
            task = asyncio.create_task(official_control.download_document(download, 1, None))
            await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert await official_control.download_document(download, 2, None) is False
            assert len(calls) == 1
        finally:
            release.set()
            if official_control._document_task:
                await official_control._document_task
            official_control._document_task = None
    asyncio.run(run())


def test_full_group_messages_dispatch_only_commands_and_preserve_passive_context():
    script = '''
import asyncio
from datetime import datetime,timezone
from types import SimpleNamespace,MethodType
from unittest.mock import AsyncMock,Mock
import nonebot
nonebot.logger.remove()
nonebot.init(driver="~aiohttp",_env_file=[],log_level="ERROR")
from nonebot.adapters.qq import Bot
from nonebot.adapters.qq.event import GroupMessageCreateEvent,GroupAtMessageCreateEvent
from hikari_bot.qq_official.plugin import register
from hikari_bot.qq_official.config import PilotConfig
config=PilotConfig('app','synthetic',groups=frozenset({'g','next-group'}))
state=SimpleNamespace(claim_event=Mock(return_value=True))
queries=SimpleNamespace(state=state,notifications=None,handle=AsyncMock(return_value=None))
client=SimpleNamespace(self_id='app',send_to_group=AsyncMock(return_value=SimpleNamespace(id='reply')))
client.send=MethodType(Bot.send,client)
def event(id,text,group='g',robot=False,at=False):
 cls=GroupAtMessageCreateEvent if at else GroupMessageCreateEvent
 return cls.model_validate(dict(id=id,content=text,group_id=group,group_openid=group,timestamp=datetime.now(timezone.utc).isoformat(),author=dict(id='member',member_openid='member',bot=robot)))
async def run():
 handler=register(config,queries=queries).handlers[0].call
 await handler(client,event('chat','今天吃什么'))
 await handler(client,event('robot','ping',robot=True))
 await handler(client,event('other','ping',group='unconfigured'))
 state.claim_event.assert_not_called()
 await handler(client,event('plain','ping'))
 await handler(client,event('plain','ping',at=True))
 assert state.claim_event.call_count==1
 args=client.send_to_group.call_args.kwargs
 assert args['group_openid']=='g' and args['msg_id']=='plain' and args['msg_seq']==1
 assert '@' not in str(args['message'])
 await handler(client,event('future','ping',group='next-group'))
 assert client.send_to_group.await_count==2
 assert client.send_to_group.call_args.kwargs['group_openid']=='next-group'
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-3000:]


def test_full_group_filter_keeps_legacy_commands_links_and_active_price_choices():
    from hikari_bot.qq_official.commands import is_group_request
    for text in ('卡密 灰流丽', '/卡图 14558127 1', '26年11月历史 player', '中译日 你好',
                 '卡组PDF #main', '关注账号', '状态', '确认操作 code', '继续',
                 'https://www.bilibili.com/video/BV1xx411c7mD', 'BV1xx411c7mD', '比赛查询'):
        assert is_group_request(text), text
    assert not is_group_request('今天吃什么')
    assert not is_group_request('1')
    assert is_group_request('1', price_active=True)
    assert is_group_request('SER', price_active=True)
