"""Apply a reviewed Git range to the independent QQ release, preserving local content."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from hikari_bot.qq_official.source_publisher import Publisher


def restart():
    subprocess.run(['systemctl', '--user', 'restart', 'srbs.service', 'hikari-output-editor.service'], check=True)


def publish(base):
    publisher = Publisher()
    current = publisher.root / 'current'
    with publisher.store.locked():
        if (publisher.store.load().get('job') or {}).get('status') in {'running', 'pending'}:
            raise RuntimeError('A source publication is in progress')
        old = current.resolve(strict=True)
        if old.parent != publisher.root / 'releases':
            raise RuntimeError('Unexpected live release path')
        head = publisher.git('rev-parse', 'HEAD').decode().strip()
        publisher.git('merge-base', '--is-ancestor', base, head)
        changes = {}
        for entry in publisher.git('diff', '--name-status', '--no-renames', base, head).decode().splitlines():
            status, name = entry.split('\t', 1)
            if status not in {'A', 'M'}:
                raise RuntimeError(f'Unexpected {status} for {name}')
            path = old / name
            if path.resolve() != path:
                raise RuntimeError(f'Symlink in release path: {name}')
            target = publisher.git('show', f'{head}:{name}')
            live = path.read_bytes() if path.exists() else None
            if live == target:
                continue
            baseline = publisher.git('show', f'{base}:{name}') if status == 'M' else None
            if live != baseline:
                raise RuntimeError(f'Independent release has different content: {name}')
            changes[name] = target
        if changes:
            new = Path(tempfile.mkdtemp(prefix=f'mycard-{head[:10]}-', dir=old.parent))
            shutil.copytree(old, new, dirs_exist_ok=True, symlinks=True)
            for name, data in changes.items():
                path = new / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            print(f'Previous release: {old}; new release: {new}', flush=True)
            publisher.point(new)
            try:
                restart()
                if not publisher.healthy():
                    raise RuntimeError('QQ services did not become healthy')
            except BaseException:
                publisher.point(old)
                restart()
                raise
        print(f'Active QQ release: {current.resolve()}', flush=True)


if __name__ == '__main__':
    publish(sys.argv[1])
