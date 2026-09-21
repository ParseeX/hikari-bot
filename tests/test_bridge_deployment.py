"""在临时 Git 仓库执行真实 update.sh，服务命令以隔离替身记录。"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipIf(os.name == 'nt', '部署脚本在 Linux 执行；服务器另外运行此测试')
class BridgeDeploymentTest(unittest.TestCase):
    def check_deployment(self, changed_path, active=True):
        script = (Path(__file__).resolve().parents[1] / 'update.sh').read_text()
        with tempfile.TemporaryDirectory(prefix='hikari-deploy-test-') as folder:
            root = Path(folder)
            source, checkout, commands = root / 'source', root / 'checkout', root / 'commands'
            source.mkdir(); commands.mkdir()
            def git(*args, cwd=source):
                return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)
            git('init', '-b', 'main')
            git('config', 'user.name', 'Deployment Test')
            git('config', 'user.email', 'test@example.invalid')
            (source / 'update.sh').write_text(script)
            (source / 'scripts/jihuanshe_bridge').mkdir(parents=True)
            (source / 'scripts/jihuanshe_bridge/worker.py').write_text('# initial')
            git('add', '.'); git('commit', '-m', 'baseline')
            git('clone', str(source), str(checkout), cwd=root)
            (source / changed_path).write_text('# changed')
            git('add', '.'); git('commit', '-m', 'change')
            log = root / 'calls'
            for name, content in {
                'uv': '#!/bin/sh\nexit 0\n',
                'sudo': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_LOG"\n',
                'systemctl': '#!/bin/sh\nif [ "$1" = is-active ]; then exit "$TEST_INACTIVE"; fi\nexit 0\n',
            }.items():
                path = commands / name; path.write_text(content); path.chmod(0o755)
            env = {**os.environ, 'HOME': str(root), 'HIKARI_BOT_DIR': str(checkout),
                   'PATH': str(commands) + os.pathsep + os.environ['PATH'],
                   'TEST_LOG': str(log), 'TEST_INACTIVE': '0' if active else '1'}
            result = subprocess.run([shutil.which('bash'), str(checkout / 'update.sh')],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text()
            self.assertIn('restart bot.service', calls)
            return calls

    def test_bot_only_update_keeps_bridge(self):
        self.assertNotIn('restart jihuanshe-bridge.service', self.check_deployment('bot.py'))

    def test_bridge_code_change_restarts_bridge(self):
        self.assertIn('restart jihuanshe-bridge.service', self.check_deployment('scripts/jihuanshe_bridge/worker.py'))

    def test_documentation_change_keeps_bridge(self):
        self.assertNotIn('restart jihuanshe-bridge.service', self.check_deployment('scripts/jihuanshe_bridge/README.md'))

    def test_inactive_bridge_is_started_even_when_code_unchanged(self):
        self.assertIn('restart jihuanshe-bridge.service', self.check_deployment('bot.py', active=False))


if __name__ == '__main__':
    unittest.main()
