"""用隔离 Git 仓库验证两个更新入口不会清掉线上文件。"""
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SafeUpdateTests(unittest.TestCase):
    def run_git(self, cwd, *args):
        return subprocess.run(['git', '-C', str(cwd), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_preserves_files_and_stops_on_conflict(self):
        tree = ast.parse((ROOT / 'hikari_bot/plugins/base.py').read_text(encoding='utf-8'))
        handlers = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and isinstance(d.func.value, ast.Name) and d.func.value.id == 'reload'
                            for d in n.decorator_list)]
        cmds = next(ast.literal_eval(n.value) for n in ast.walk(handlers[0])
                    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'cmds'
                                                       for t in n.targets))
        for entry in ('reload', 'deploy'):
            for scenario in ('untracked', 'unrelated-edit', 'conflict', 'diverged'):
                with self.subTest(entry=entry, scenario=scenario), tempfile.TemporaryDirectory() as td:
                    root = Path(td); upstream = root / 'upstream'; upstream.mkdir()
                    self.run_git(upstream, 'init', '-b', 'main')
                    self.run_git(upstream, 'config', 'user.email', 'test@example.invalid')
                    self.run_git(upstream, 'config', 'user.name', 'Test')
                    (upstream / 'api.py').write_text('original\n')
                    (upstream / 'other.py').write_text('original\n')
                    self.run_git(upstream, 'add', '.')
                    self.run_git(upstream, 'commit', '-m', 'initial')
                    checkout = root / 'checkout'
                    self.run_git(root, 'clone', str(upstream), str(checkout))
                    self.run_git(checkout, 'config', 'user.email', 'test@example.invalid')
                    self.run_git(checkout, 'config', 'user.name', 'Test')
                    (checkout / 'untracked-api.py').write_text('preserve me\n')
                    target = checkout / ('other.py' if scenario == 'unrelated-edit' else 'api.py')
                    if scenario != 'untracked': target.write_text('local change\n')
                    if scenario == 'diverged':
                        self.run_git(checkout, 'add', 'api.py')
                        self.run_git(checkout, 'commit', '-m', 'local')
                    (upstream / 'api.py').write_text('upstream change\n')
                    self.run_git(upstream, 'commit', '-am', 'upstream')
                    bins = root / 'bin'; bins.mkdir()
                    marker = root / 'effects'
                    for name in ('uv', 'sudo', 'systemctl'):
                        stub = bins / name
                        stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + '" >> "$EFFECTS"\nexit 0\n')
                        stub.chmod(0o755)
                    env = {**os.environ, 'PATH': str(bins) + ':' + os.environ['PATH'],
                           'HOME': str(root), 'HIKARI_BOT_DIR': str(checkout), 'EFFECTS': str(marker)}
                    if entry == 'deploy':
                        result = subprocess.run(['bash', str(ROOT / 'update.sh')], cwd=checkout,
                                                env=env, capture_output=True)
                    else:
                        for cmd in cmds:
                            result = subprocess.run(cmd, cwd=checkout, env=env, capture_output=True)
                            if result.returncode: break
                    self.assertEqual((checkout / 'untracked-api.py').read_text(), 'preserve me\n')
                    if scenario in ('conflict', 'diverged'):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(target.read_text(), 'local change\n')
                        self.assertFalse(marker.exists(), '冲突时不能安装依赖或重启服务')
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr.decode())
                        self.assertEqual((checkout / 'api.py').read_text(), 'upstream change\n')
                        if scenario == 'unrelated-edit': self.assertEqual(target.read_text(), 'local change\n')


if __name__ == '__main__':
    unittest.main()
