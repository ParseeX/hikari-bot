"""独立发布进程：固定目录、Git 留档、原子切换与失败恢复，不在网页进程执行草稿。"""
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
import urllib.request

from .source_catalog import checked_path
from .source_editor import SourceStore, check, digest


class Publisher:
    def __init__(self, home=None, *, runner=None):
        self.home = Path(home) if home else Path.home()
        self.root = self.home/'srbs'
        self.backend = self.home/'hikari-bot'
        self.store = SourceStore(self.home, self.root/'current')
        self.runner = runner or subprocess.run

    def run(self, *args, timeout=60):
        return self.runner(args, check=True, capture_output=True, timeout=timeout).stdout

    def git(self, *args):
        return self.run('git', '-C', str(self.backend), *args)

    def clean(self):
        # 只要存在未提交或未跟踪文件就停下，绝不 reset/clean/stash。
        if self.git('status', '--porcelain'):
            raise ValueError('后台仓库存在未提交修改，需要先合并；本次未覆盖它们')
        if self.git('branch','--show-current').decode().strip() != 'main':
            raise ValueError('后台不在预期的 main 分支')

    def point(self, target):
        if not target.is_dir() or target.parent != self.root/'releases':
            raise ValueError('发布目录无效')
        pending = self.root/'current.source-pending'
        if pending.exists() or pending.is_symlink():
            raise ValueError('上次发布指针尚未处理')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, self.root/'current')

    def restart(self):
        self.run('sudo', '-n', 'systemctl', 'restart', 'bot.service', timeout=120)
        self.run('systemctl', '--user', 'restart', 'srbs.service', 'hikari-output-editor.service', timeout=120)

    def healthy(self):
        stable = 0
        for _ in range(30):
            try:
                active = self.run('systemctl', '--user', 'is-active', 'srbs.service', 'hikari-output-editor.service').splitlines()
                backend = self.run('systemctl', 'is-active', 'bot.service').strip()
                with urllib.request.urlopen('http://127.0.0.1:8792/', timeout=3) as response:
                    ok = response.status == 200 and active == [b'active', b'active'] and backend == b'active'
                stable = stable+1 if ok else 0
                if stable >= 3:
                    return True
            except (OSError, subprocess.SubprocessError):
                stable = 0
            time.sleep(2)
        return False

    def update(self, job_id, **changes):
        with self.store.locked():
            state = self.store.load()
            if not state.get('job') or state['job']['id'] != job_id:
                raise ValueError('发布任务已变化')
            state['job'].update(changes)
            state['revision'] += 1
            self.store.write(state)

    def tick(self):
        with self.store.locked():
            state = self.store.load()
            job = state.get('job')
            if not job or job['status'] != 'pending':
                return
            job.update(status='running', note='正在检查源码、创建版本并重载服务。')
            state['revision'] += 1
            self.store.write(state)
        try:
            if job['operation'] == 'rollback':
                self.rollback(job)
            else:
                self.publish(job)
        except Exception:
            # 不把 Git/用户代码异常正文反射到网页，避免泄露运行信息或凭据。
            latest = self.store.load()['job']
            if latest['status'] == 'running':
                self.update(job['id'], status='failed', note='发布前检查失败，未切换版本。可能存在源码冲突、语法错误或仓库改动。')

    def publish(self, job):
        self.clean()
        old = (self.root/'current').resolve(strict=True)
        if str(old) != job['release']:
            raise ValueError('运行版本已变化')
        for name in self.git('ls-files').decode().splitlines():
            deployed = old/name
            repository = self.backend/name
            if not deployed.is_file() or deployed.is_symlink() or repository.is_symlink():
                raise ValueError('发布目录与后台源码不一致')
            if deployed.read_bytes().replace(b'\r\n',b'\n') != repository.read_bytes().replace(b'\r\n',b'\n'):
                raise ValueError('发布目录包含其他代码修改，需要先合并')
        previous = self.git('rev-parse', 'HEAD').decode().strip()
        changes = job['files']
        if not changes:
            raise ValueError('没有待发布文件')
        for name, item in changes.items():
            live = checked_path(old, name).read_text(encoding='utf-8')
            repository = checked_path(self.backend, name).read_text(encoding='utf-8')
            if digest(live) != item['base'] or repository != live or not check(name, item['text'])['ok']:
                raise ValueError('代码已变化或检查未通过')
        backup = self.root/'deploy-backups'/('source-'+job['id'])
        backup.mkdir(mode=0o700)
        new = self.root/'releases'/('source-'+job['id'])
        new.mkdir()
        self.git('archive', '--format=tar', '-o', str(backup/'before.tar'), previous)
        with tarfile.open(backup/'before.tar') as archive:
            archive.extractall(new, filter='data')
        originals = {name:checked_path(self.backend,name).read_bytes() for name in changes}
        record = {'previous_release':str(old), 'release':str(new), 'previous_commit':previous, 'paths':list(changes)}
        (backup/'before.json').write_text(json.dumps(record,indent=2)+'\n')
        for name, item in changes.items():
            checked_path(new,name).write_text(item['text'],encoding='utf-8')
        committed = None
        switched = False
        try:
            self.clean()
            if self.git('rev-parse','HEAD').decode().strip() != previous:
                raise ValueError('Git 版本已变化')
            for name, item in changes.items():
                checked_path(self.backend,name).write_text(item['text'],encoding='utf-8')
            self.git('add','--',*changes)
            self.git('commit','-m',f'后台发布 {job["feature"]} 功能源码 [skip ci]')
            committed = self.git('rev-parse','HEAD').decode().strip()
            record['commit'] = committed
            (backup/'before.json').write_text(json.dumps(record,indent=2)+'\n')
            self.update(job['id'], note='代码已留档，正在重载并检查服务。', commit=committed[:12])
            self.point(new)
            switched = True
            self.restart()
            if not self.healthy():
                raise RuntimeError('服务启动检查未通过')
        except Exception:
            try:
                if switched:
                    self.point(old)
                if committed:
                    self.clean()
                    if self.git('rev-parse','HEAD').decode().strip() != committed:
                        raise ValueError('仓库出现其他提交')
                    self.git('revert','--no-commit',committed)
                    self.git('commit','-m','恢复启动失败前的功能源码 [skip ci]')
                else:
                    for name, content in originals.items():
                        if checked_path(self.backend,name).read_text(encoding='utf-8') != changes[name]['text']:
                            raise ValueError('文件被其他进程修改')
                    for name, content in originals.items():
                        checked_path(self.backend,name).write_bytes(content)
                    self.git('add','--',*changes)
                if switched:
                    self.restart()
                self.update(job['id'],status='failed',note='发布或启动检查失败，已恢复原代码；代码造成的数据或外部副作用不能靠回退撤销。')
            except Exception:
                self.update(job['id'],status='recovery_required',note='恢复时发现冲突，请由维护者检查；未覆盖后续代码修改。')
            return
        pushed = self.push()
        with self.store.locked():
            state = self.store.load()
            for name, item in changes.items():
                if state['drafts'].get(name) == item:
                    state['drafts'].pop(name)
            state['last_success'] = record
            state['job'].update(status='published',commit=committed[:12],pushed=pushed,
                                note='源码已发布并重载，服务启动检查通过。'+('Git 已推送。' if pushed else 'Git 已本地提交，推送失败，需维护者同步。')+' 实际命令行为仍需使用验证。')
            state['revision'] += 1
            self.store.write(state)

    def push(self):
        try:
            self.git('push','origin','HEAD:main')
            return True
        except subprocess.SubprocessError:
            return False

    def rollback(self, job):
        previous = job['previous']
        self.clean()
        if (str((self.root/'current').resolve()) != previous['release'] or
                self.git('rev-parse','HEAD').decode().strip() != previous['commit']):
            raise ValueError('运行版本已变化，不能覆盖后续发布')
        target = Path(previous['previous_release'])
        if not target.is_dir():
            raise ValueError('上一版目录不存在')
        # 后台仓库的前一个提交也必须与记录一致，拒绝跨越其他改动。
        if self.git('rev-parse','HEAD^').decode().strip() != previous['previous_commit']:
            raise ValueError('提交记录不匹配')
        try:
            self.git('revert','--no-commit',previous['commit'])
            self.git('commit','-m','从后台恢复上一版功能源码 [skip ci]')
            self.point(target)
            self.restart()
            healthy = self.healthy()
        except Exception:
            self.update(job['id'],status='recovery_required',note='回退步骤未完成，需维护者检查 Git 与运行指针；系统不会重复执行。')
            return
        commit = self.git('rev-parse','HEAD').decode().strip()
        pushed = self.push()
        with self.store.locked():
            state = self.store.load()
            state['last_success'] = None
            state['job'].update(status='rolled_back' if healthy else 'recovery_required',commit=commit[:12],pushed=pushed,
                                note='已恢复上一版源码。'+('服务启动检查通过。' if healthy else '服务启动未确认，需要维护者检查。')+'草稿及业务数据未回退。')
            state['revision'] += 1
            self.store.write(state)

    def serve(self):
        with self.store.locked():
            state = self.store.load()
            if state.get('job') and state['job']['status'] == 'running':
                state['job'].update(status='recovery_required',note='上次发布被中断，结果未确认。请维护者检查版本后恢复，系统不会重复执行。')
                state['revision'] += 1
                self.store.write(state)
        while True:
            try:
                self.tick()
            except Exception:
                print('source publisher check failed',flush=True)
            time.sleep(2)
