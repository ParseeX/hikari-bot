"""批量采集的断点、重试、入库回滚与互斥验证，不访问手机。"""
from dataclasses import asdict
import io
import json
import urllib.error

import pytest

from scripts.card_catalog import catalog
from scripts.card_catalog import crawl_jhs as crawl


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / 'catalog.sqlite3'
    db = catalog.connect(path)
    with db:
        catalog.import_cards(db, {'1': {'cid': 1, 'id': 89631139, 'cn_name': '青眼白龙',
            'data': {'type': 17, 'ot': 11}, 'text': {}}}, {})
    yield db, path, tmp_path / 'progress.json'
    db.close()


def run(setup, packs, sync, **kwargs):
    db, path, state = setup
    return crawl.run_batch(db, packs, database=path, state_path=state,
                           bridge_env=None, sync=sync, sleep=lambda _: None,
                           emit=lambda *args, **kw: None, **kwargs)


def import_one(db, args, snapshots):
    return catalog.import_pack(db, args.prefix, [{'id': sum(map(ord, args.prefix)),
        'card_id': 139, 'number': args.prefix + '-JP001（异画）',
        'rarity': 'S1R（代标）', 'name_cn': '青眼白龙'}],
        expected_versions=args.expected_versions)


def test_packs_accept_text_and_json_but_reject_conflicts(tmp_path):
    text = tmp_path / 'packs.txt'
    text.write_text('# 盒号\ndbgv\nROTA # 备注\nDBGV\n', encoding='utf-8-sig')
    assert [p.prefix for p in crawl.load_packs([], text)] == ['DBGV', 'ROTA']
    path = tmp_path / 'packs.json'
    path.write_text('[{"prefix":"DBGV","expected_versions":92}]')
    assert crawl.load_packs([], path) == [crawl.Pack('DBGV', expected_versions=92)]
    with pytest.raises(ValueError):
        crawl.load_packs(['DBGV'], path)
    for invalid in ['../DBGV', '', {'prefix': 'X', 'expected_cards': True}, {'prefix': 'X', 'unknown': 1}]:
        with pytest.raises(ValueError):
            crawl.parse_pack(invalid)


def test_product_jobs_support_no_prefix_and_preserve_legacy_checkpoints(tmp_path):
    pack = crawl.parse_pack({'jhs_pack_id': 4404, 'name': 'EX 復刻版'})
    assert pack.prefix is None and pack.key == 'jhs:4404'
    state = {'jobs': {'DBGV': {'status': 'done', 'spec': {
        'prefix': 'DBGV', 'expected_cards': None, 'expected_versions': None,
        'name': None, 'release_date': None, 'source_url': None}}}}
    assert crawl.pending_packs([crawl.Pack('DBGV')], state) == []
    assert crawl.pending_packs([pack], state) == [pack]
    path = tmp_path / 'packs.json'
    path.write_text('[{"jhs_pack_id":4404},{"jhs_pack_id":4405}]')
    assert len(crawl.load_packs([], path)) == 2
    for bad in [True, 0, -1, '4404']:
        with pytest.raises(ValueError):
            crawl.parse_pack({'jhs_pack_id': bad})
    backfill = crawl.parse_pack({'prefix': 'DBGV', 'backfill': True})
    assert backfill.key == 'backfill:DBGV'
    assert crawl.pending_packs([backfill], state) == [backfill]
    for bad in [{'backfill': True}, {'prefix': 'DBGV', 'backfill': 'yes'},
                {'prefix': 'DBGV', 'backfill': True, 'jhs_pack_id': 4404}]:
        with pytest.raises(ValueError):
            crawl.parse_pack(bad)


def test_completed_packs_are_skipped_and_refresh_is_explicit(setup):
    packs = [crawl.Pack('A'), crawl.Pack('B')]
    assert run(setup, packs, import_one) == 0
    db, path, state = setup
    assert db.execute('SELECT count(*) FROM jhs_versions').fetchone()[0] == 2
    saved = crawl.read_state(state, path)
    assert all(job['status'] == 'done' for job in saved['jobs'].values())
    backups = list((path.parent / 'backups').iterdir())
    def forbidden(*args):
        raise AssertionError('已完成任务不应重新请求手机')
    assert run(setup, packs, forbidden) == 0
    assert list((path.parent / 'backups').iterdir()) == backups
    assert run(setup, packs, import_one, refresh=True) == 0
    assert db.execute('SELECT count(*) FROM jhs_versions').fetchone()[0] == 2
    row = db.execute('SELECT number_raw,rarity_raw FROM jhs_versions LIMIT 1').fetchone()
    assert row[0].endswith('（异画）') and row[1] == 'S1R（代标）'


def test_network_failure_retries_then_completes(setup):
    calls = []
    def flaky(db, args, snapshots):
        calls.append(args.prefix)
        if len(calls) < 3:
            raise urllib.error.URLError('sensitive-upstream-value')
        return import_one(db, args, snapshots)
    assert run(setup, [crawl.Pack('A')], flaky) == 0
    state = json.loads(setup[2].read_text())
    assert calls == ['A', 'A', 'A']
    assert state['jobs']['A']['attempts'] == 3
    assert 'error' not in state['jobs']['A']


def test_auth_failure_stops_without_retry_or_leaking_details(setup):
    calls = []
    def forbidden(db, args, snapshots):
        calls.append(args.prefix)
        raise urllib.error.HTTPError('https://example.invalid/secret', 401, 'token-secret', {}, io.BytesIO())
    assert run(setup, [crawl.Pack('A'), crawl.Pack('B')], forbidden) == 1
    assert calls == ['A']
    raw = setup[2].read_text()
    assert 'HTTP 401' in raw and 'secret' not in raw


def test_incomplete_pack_fails_without_partial_insert_and_resume_retries(setup):
    pack = crawl.Pack('A', expected_versions=2)
    calls = []
    def incomplete(db, args, snapshots):
        calls.append(args.prefix)
        return import_one(db, args, snapshots)
    assert run(setup, [pack], incomplete) == 1
    assert calls == ['A']  # 校验失败不反复请求相同结果。
    assert setup[0].execute('SELECT count(*) FROM jhs_versions').fetchone()[0] == 0
    assert run(setup, [crawl.Pack('A', expected_versions=1)], import_one) == 0


def test_interrupt_keeps_finished_pack_and_replays_only_interrupted_pack(setup):
    calls = []
    def interrupted(db, args, snapshots):
        calls.append(args.prefix)
        if args.prefix == 'B':
            raise KeyboardInterrupt
        return import_one(db, args, snapshots)
    packs = [crawl.Pack('A'), crawl.Pack('B')]
    assert run(setup, packs, interrupted) == 130
    saved = json.loads(setup[2].read_text())
    assert saved['jobs']['A']['status'] == 'done'
    assert saved['jobs']['B']['status'] == 'interrupted'
    calls.clear()
    def resumed(db, args, snapshots):
        calls.append(args.prefix)
        return import_one(db, args, snapshots)
    assert run(setup, packs, resumed) == 0
    assert calls == ['B']


def test_running_checkpoint_is_replayed_and_changed_spec_requires_refresh(setup):
    _, path, state = setup
    pack = crawl.Pack('A')
    crawl.save_state(state, {'format': 1, 'database': str(path.resolve()),
                            'jobs': {'A': {'status': 'running', 'spec': asdict(pack)}}})
    assert run(setup, [pack], import_one) == 0
    saved = crawl.read_state(state, path)
    assert crawl.pending_packs([crawl.Pack('A', expected_versions=1)], saved)


def test_repeated_failures_stop_before_remaining_packs(setup):
    calls = []
    def offline(db, args, snapshots):
        calls.append(args.prefix)
        raise urllib.error.URLError('offline')
    assert run(setup, [crawl.Pack(c) for c in 'ABCD'], offline, attempts=2, max_failures=2) == 1
    assert calls == ['A', 'A', 'B', 'B']


def test_state_cannot_be_reused_for_another_database(setup, tmp_path):
    run(setup, [crawl.Pack('A')], import_one)
    with pytest.raises(ValueError, match='另一个数据库'):
        crawl.read_state(setup[2], tmp_path / 'other.sqlite3')


def test_run_lock_prevents_two_collectors_and_releases_on_exit(setup):
    with crawl.exclusive_run(setup[1]):
        with pytest.raises(RuntimeError, match='已有'):
            with crawl.exclusive_run(setup[1]):
                pass
    with crawl.exclusive_run(setup[1]):
        pass


def test_dry_run_and_status_do_not_open_or_modify_database(setup, monkeypatch, capsys):
    _, path, state = setup
    def forbidden(*args):
        raise AssertionError('只读预览不能打开写连接或读取桥接凭据')
    monkeypatch.setattr(catalog, 'connect', forbidden)
    monkeypatch.setattr(catalog, 'read_bridge_env', forbidden)
    monkeypatch.setattr('sys.argv', ['crawl_jhs.py', '--db', str(path), '--state', str(state), '--packs', 'DBGV', '--dry-run'])
    assert crawl.main() == 0
    assert json.loads(capsys.readouterr().out)['pending'] == ['DBGV']
    assert not state.exists()
