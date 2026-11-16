"""真实临时 Git 仓库验证发布与回退；不启动服务、不发送消息、不接触业务数据库。"""
import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from hikari_bot.qq_official.source_catalog import ALLOWED, checked_path
from hikari_bot.qq_official.source_editor import SourceStore, check, digest
from hikari_bot.qq_official.source_publisher import Publisher
from hikari_bot.qq_official.editor_web import create_app
from hikari_bot.qq_official.output_templates import Store
from hikari_bot.services.mycard_archive.admin_auth import AdminAuth, password_record

NAME='hikari_bot/services/mycard_reports.py'
ORIGIN='https://bot.xyk.one'


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def setup(tmp_path):
    home=tmp_path/'home';repo=home/'hikari-bot';repo.mkdir(parents=True)
    git(repo,'init','-b','main');git(repo,'config','user.email','test@example.invalid');git(repo,'config','user.name','Synthetic test')
    git(repo,'config','core.autocrlf','false')
    path=repo/NAME;path.parent.mkdir(parents=True);path.write_text('def history():\n    return "old"\n',encoding='utf-8')
    git(repo,'add','.');git(repo,'commit','-m','baseline')
    bare=tmp_path/'remote.git';bare.mkdir();git(bare,'init','--bare');git(repo,'remote','add','origin',str(bare));git(repo,'push','-u','origin','main')
    release=home/'srbs/releases/baseline';release.mkdir(parents=True)
    target=release/NAME;target.parent.mkdir(parents=True);shutil.copy2(path,target)
    (home/'srbs/deploy-backups').mkdir()
    try:(home/'srbs/current').symlink_to(release,target_is_directory=True)
    except OSError:pytest.skip('本机未启用目录符号链接权限；Linux 覆盖发布事务')
    store=SourceStore(home,home/'srbs/current')
    publisher=Publisher(home)
    publisher.restart=lambda:None
    publisher.healthy=lambda:True
    return home,repo,store,publisher


def save(store,text='def history():\n    return "new"\n'):
    current=store.read('history',NAME)
    return store.save('history',NAME,text,current['base'],current['revision'])


def test_catalog_points_only_to_existing_repository_sources():
    root=Path(__file__).resolve().parents[1]
    for name in ALLOWED:
        assert checked_path(root,name).is_file()


def test_check_never_executes_and_reports_syntax_position(tmp_path):
    marker=tmp_path/'must-not-exist'
    result=check(NAME,f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
    assert result['ok'] and not marker.exists()
    assert not check(NAME,'def broken(:\n')['ok']
    assert check(NAME,'\ufeffdef valid():\n    return 1\n')['ok']
    assert not check('output_schema.json','{')['ok']


def test_drafts_conflicts_and_feature_path_boundary(setup):
    home,repo,store,publisher=setup
    first=store.read('history',NAME)
    save(store)
    assert store.current(NAME)==first['text']
    assert store.read('winrate',NAME)['text'].endswith('"new"\n')
    with pytest.raises(ValueError,match='变化'):store.save('history',NAME,'pass\n',first['base'],first['revision'])
    for path in ['../../.env','/etc/passwd','hikari_bot/qq_official/editor_web.py']:
        with pytest.raises(ValueError):store.read('history',path)
    with pytest.raises(ValueError):store.read('tournament',NAME)
    checked_path(store.root,NAME).write_text('pass\n')
    assert store.read('history',NAME)['conflict']
    with pytest.raises(ValueError):store.enqueue('history',store.load()['revision'])


def test_publish_and_rollback_use_real_git_preserve_templates_and_data(setup):
    home,repo,store,publisher=setup
    original=store.current(NAME)
    business=repo/'records.sqlite3';business.write_bytes(b'synthetic business records')
    # 正常运行数据被 Git 忽略，与生产目录行为一致。
    git(repo,'config','core.excludesFile',str(home/'ignore'))
    (home/'ignore').write_text('records.sqlite3\n')
    template=home/'.config/srbs/output-templates.json';template.parent.mkdir(parents=True);template.write_text('{"custom":"untouched"}')
    save(store)
    store.enqueue('history',store.load()['revision'])
    publisher.tick()
    state=store.load();assert state['job']['status']=='published' and state['job']['pushed']
    assert 'new' in store.current(NAME) and 'new' in (repo/NAME).read_text()
    assert not git(repo,'status','--porcelain')
    assert '[skip ci]' in git(repo,'log','-1','--format=%s')
    assert 'files' not in store.listing('history')['job']
    store.enqueue('history',state['revision'],'rollback');publisher.tick()
    assert store.load()['job']['status']=='rolled_back'
    assert store.current(NAME)==original and (repo/NAME).read_text()==original
    assert template.read_text()=='{"custom":"untouched"}' and business.read_bytes()==b'synthetic business records'
    assert '[skip ci]' in git(repo,'log','-1','--format=%s')


def test_failed_start_restores_code_and_keeps_draft(setup):
    home,repo,store,publisher=setup
    original=store.current(NAME);save(store);store.enqueue('history',store.load()['revision'])
    publisher.healthy=lambda:False
    publisher.tick()
    assert store.load()['job']['status']=='failed'
    assert store.current(NAME)==original and (repo/NAME).read_text()==original
    assert NAME in store.load()['drafts'] and not git(repo,'status','--porcelain')


def test_repository_drift_and_busy_job_never_overwrite(setup):
    home,repo,store,publisher=setup
    save(store);store.enqueue('history',store.load()['revision'])
    with pytest.raises(ValueError):save(store,'pass\n')
    (repo/'someone-elses-work.py').write_text('preserve me')
    publisher.tick()
    assert store.load()['job']['status']=='failed' and 'old' in store.current(NAME)
    assert (repo/'someone-elses-work.py').read_text()=='preserve me'


def test_web_source_auth_origin_save_check_and_publish_queue(setup,tmp_path):
    home,repo,store,publisher=setup
    auth=AdminAuth(tmp_path/'auth');auth.password_path.parent.mkdir(parents=True,exist_ok=True)
    auth.password_path.write_text(json.dumps(password_record('synthetic-source-password')))
    client=TestClient(create_app(store=Store(home),auth=auth,source_store=store),base_url=ORIGIN)
    def post(body):return client.post('/api/source',json=body,headers={'Origin':ORIGIN})
    assert post({'operation':'read','feature':'history','path':NAME}).status_code==401
    client.post('/api/login',json={'password':'synthetic-source-password'},headers={'Origin':ORIGIN})
    assert client.post('/api/source',json={'operation':'list','feature':'history'}).status_code==403
    current=post({'operation':'read','feature':'history','path':NAME}).json()
    response=post({'operation':'save','feature':'history','path':NAME,'text':'def bad(:','base':current['base'],'revision':current['revision']})
    assert response.status_code==200
    assert post({'operation':'publish','feature':'history','revision':response.json()['revision']}).status_code==409
    assert not store.load()['job']
    assert post({'operation':'read','feature':'history','path':'../.env'}).status_code==409
    assert client.get('/assets/source.js').status_code==200
