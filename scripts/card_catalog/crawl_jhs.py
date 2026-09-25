"""按卡盒批量采集集换社版本，串行查询并保存断点。仅使用标准库。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import urllib.error

if __package__:
    from . import catalog
else:
    import catalog


@dataclass(frozen=True)
class Pack:
    prefix: str | None = None
    expected_cards: int | None = None
    expected_versions: int | None = None
    name: str | None = None
    release_date: str | None = None
    source_url: str | None = None
    jhs_pack_id: int | None = None
    konami_pid: str | None = None
    backfill: bool = False

    @property
    def key(self):
        if self.backfill:
            return f'backfill:{self.prefix}'
        return f'jhs:{self.jhs_pack_id}' if self.jhs_pack_id else self.prefix


def parse_pack(value) -> Pack:
    if isinstance(value, str):
        value = {'prefix': value}
    if not isinstance(value, dict) or set(value) - set(Pack.__dataclass_fields__):
        raise ValueError('卡盒条目格式错误或包含未知字段')
    prefix = str(value.get('prefix') or '').strip().upper() or None
    pack_id = value.get('jhs_pack_id')
    if type(value.get('backfill', False)) is not bool:
        raise ValueError('backfill 必须为布尔值')
    if value.get('backfill') and (not prefix or pack_id is not None):
        raise ValueError('补齐任务需要盒号，不能同时指定商品 ID')
    if pack_id is not None and (type(pack_id) is not int or not 0 < pack_id < 2**53):
        raise ValueError('jhs_pack_id 必须为正整数')
    if (prefix is None and pack_id is None) or (prefix and not re.fullmatch('[A-Z0-9]{1,16}', prefix)):
        raise ValueError('卡盒前缀须为 1 至 16 位英文字母或数字')
    for key in ('expected_cards', 'expected_versions'):
        count = value.get(key)
        if count is not None and (type(count) is not int or count <= 0):
            raise ValueError(f'{key} 必须为正整数')
    for key in ('name', 'release_date', 'source_url', 'konami_pid'):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError(f'{key} 必须为字符串')
    return Pack(**{**value, 'prefix': prefix})


def load_packs(prefixes, path=None):
    values = list(prefixes or [])
    if path:
        text = path.read_text(encoding='utf-8-sig')
        if path.suffix.lower() == '.json':
            more = json.loads(text)
            if not isinstance(more, list):
                raise ValueError('JSON 清单必须为数组')
        else:
            more = [line.split('#', 1)[0].strip() for line in text.splitlines()]
            more = [line for line in more if line]
        values.extend(more)
    unique = {}
    for value in values:
        pack = parse_pack(value)
        if pack.key in unique and unique[pack.key] != pack:
            raise ValueError(f'{pack.key} 在清单中有不同校验要求，请保留一个条目')
        unique[pack.key] = pack
    return list(unique.values())


def read_state(path, database):
    if not path.exists():
        return {'format': 1, 'database': str(database.resolve()), 'jobs': {}}
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or state.get('format') != 1 or not isinstance(state.get('jobs'), dict):
        raise ValueError('进度文件格式错误，请保留原文件并检查')
    if state.get('database') != str(database.resolve()):
        raise ValueError('进度文件属于另一个数据库，不能复用')
    for prefix, job in state['jobs'].items():
        if not isinstance(job, dict) or job.get('status') not in {'running', 'done', 'failed', 'interrupted'}:
            raise ValueError('进度文件中的任务状态无效')
        if parse_pack(job.get('spec')).key != prefix:
            raise ValueError('进度文件中的盒号不一致')
    return state


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump(state, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


@contextmanager
def exclusive_run(database):
    """同一个数据库仅允许运行一个批量采集进程；进程退出即释放锁。"""
    path = database.resolve().with_suffix('.jhs-crawl.lock')
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if not path.stat().st_size:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('已有批量采集任务运行中') from None
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('已有批量采集任务运行中') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pending_packs(packs, state, refresh=False):
    return [pack for pack in packs if refresh or
            state['jobs'].get(pack.key, {}).get('status') != 'done' or
            parse_pack(state['jobs'][pack.key].get('spec')) != pack]


def error_info(error):
    """只保存分类，不记录响应正文、认证头或可能携带凭据的 URL。"""
    if isinstance(error, urllib.error.HTTPError):
        status = error.code
        error.close()
        return f'HTTP {status}', status in {408, 429, 500, 502, 503, 504}, status in {401, 403}
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return '连接中断或请求超时', True, False
    if isinstance(error, ValueError):
        return '返回数据或卡盒校验失败；请核对数量、原始快照和已有映射', False, False
    if isinstance(error, sqlite3.Error):
        return '数据库操作失败', False, True
    return '本地文件操作失败', False, True


def run_batch(db, packs, *, database, state_path, bridge_env, interval=10,
              attempts=3, max_failures=3, refresh=False, sync=catalog.sync_pack,
              sleep=time.sleep, emit=print):
    state = read_state(state_path, database)
    pending = pending_packs(packs, state, refresh)
    counts = {'selected': len(packs), 'skipped': len(packs) - len(pending), 'done': 0, 'failed': 0}
    if not pending:
        emit(json.dumps(counts, ensure_ascii=False), flush=True)
        return 0
    # 一轮备份一次；单盒导入由已有事务保证，不因每盒重复备份整个主库。
    catalog.backup(db, database)
    consecutive = 0
    try:
        for position, pack in enumerate(pending):
            if position:
                sleep(interval)
            job = state['jobs'][pack.key] = {'spec': asdict(pack), 'status': 'running',
                                               'started_at': catalog.now(), 'attempts': 0}
            for attempt in range(1, attempts + 1):
                job.update(status='running', attempts=attempt)
                save_state(state_path, state)
                emit(f'[{position + 1}/{len(pending)}] {pack.key} 开始，第 {attempt} 次尝试', flush=True)
                try:
                    result = sync(db, SimpleNamespace(**asdict(pack), bridge_env=bridge_env),
                                  database.parent / 'source-snapshots')
                except (ValueError, OSError, sqlite3.Error) as error:
                    reason, retry, fatal = error_info(error)
                    job.update(status='failed', error=reason, finished_at=catalog.now())
                    save_state(state_path, state)
                    emit(f'{pack.key} 失败：{reason}', flush=True)
                    if retry and attempt < attempts:
                        sleep(max(interval, min(300, 10 * 2 ** (attempt - 1))))
                        continue
                    counts['failed'] += 1
                    consecutive += 1
                    if fatal or consecutive >= max_failures:
                        emit('采集已停止，修复后重跑同一命令可继续。', flush=True)
                        emit(json.dumps(counts, ensure_ascii=False), flush=True)
                        return 1
                    break
                else:
                    job.pop('error', None)
                    job.update(status='done', result=result, finished_at=catalog.now())
                    save_state(state_path, state)
                    counts['done'] += 1
                    consecutive = 0
                    emit(f"{pack.key} 完成：{result['cards']} 张卡、{result['versions']} 个版本，"
                         f"{result.get('unmatched_cards', 0)} 张卡待匹配", flush=True)
                    break
    except KeyboardInterrupt:
        for job in state['jobs'].values():
            if job['status'] == 'running':
                job.update(status='interrupted', finished_at=catalog.now())
        save_state(state_path, state)
        emit('已保存断点，重跑同一命令可继续。', flush=True)
        return 130
    emit(json.dumps(counts, ensure_ascii=False), flush=True)
    return 1 if counts['failed'] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--bridge-env', type=Path)
    parser.add_argument('--packs', nargs='+', default=[])
    parser.add_argument('--packs-file', type=Path)
    parser.add_argument('--state', type=Path, help='默认位于主库旁的 jhs-crawl-state.json')
    parser.add_argument('--interval', type=float, default=10, help='卡盒之间的等待秒数，至少 1 秒')
    parser.add_argument('--attempts', type=int, default=3, help='每盒每轮最多尝试次数')
    parser.add_argument('--max-failures', type=int, default=3, help='连续失败多少盒后停止')
    parser.add_argument('--refresh', action='store_true', help='重新查询清单中已完成的盒子')
    parser.add_argument('--dry-run', action='store_true', help='只查看队列，不查询或写入')
    parser.add_argument('--status', action='store_true', help='只查看已保存进度')
    args = parser.parse_args()
    database = args.db.expanduser().resolve()
    state_path = (args.state or database.parent / 'jhs-crawl-state.json').expanduser().resolve()
    if state_path == database or state_path == database.with_suffix('.jhs-crawl.lock'):
        parser.error('进度文件不能覆盖数据库或锁文件')
    if not database.is_file():
        parser.error('主库不存在，请先执行 catalog.py sync-base')
    if not math.isfinite(args.interval) or args.interval < 1 or not 1 <= args.attempts <= 10 or args.max_failures < 1:
        parser.error('interval 至少 1 秒，attempts 为 1 至 10，max-failures 至少为 1')
    state = read_state(state_path, database)
    if args.status:
        # 不显示源 URL 等可由清单自行添加的内容，只显示任务摘要。
        print(json.dumps({key: {field: job.get(field) for field in ('status', 'attempts', 'finished_at', 'result', 'error')}
                          for key, job in state['jobs'].items()}, ensure_ascii=False, indent=2))
        return 0
    packs = load_packs(args.packs, args.packs_file)
    if not packs:
        parser.error('请使用 --packs 或 --packs-file 提供盒号清单')
    if args.dry_run:
        print(json.dumps({'pending': [p.key for p in pending_packs(packs, state, args.refresh)],
                          'selected': len(packs)}, ensure_ascii=False))
        return 0
    catalog.read_bridge_env(args.bridge_env)  # 启动前检查配置，绝不输出其内容。
    with exclusive_run(database):
        db = catalog.connect(database)
        try:
            return run_batch(db, packs, database=database, state_path=state_path,
                             bridge_env=args.bridge_env, interval=args.interval,
                             attempts=args.attempts, max_failures=args.max_failures, refresh=args.refresh)
        finally:
            db.close()


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as error:
        # 错误正文可能含第三方内容，顶层仅显示异常分类。
        raise SystemExit(f'采集未完成：{type(error).__name__}，请检查配置、进度文件或运行锁。') from None
