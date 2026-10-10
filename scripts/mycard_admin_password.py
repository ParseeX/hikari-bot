"""在本机隐藏输入密码，仅将 scrypt 哈希通过 SSH 写入服务器。"""
import argparse
import getpass
import json
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hikari_bot.services.mycard_archive.admin_auth import password_record  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh', default='server')
    parser.add_argument('--root', default='/home/xyk/.local/share/hikari-mycard')
    parser.add_argument('--keep-open', action='store_true')
    args = parser.parse_args()
    try:
        print('设置 https://ygo.xyk.one/mc/admin 管理密码（至少 10 个字符，输入不显示）')
        password = getpass.getpass('新密码：')
        if password != getpass.getpass('再次输入：'):
            raise ValueError('两次输入不一致，请重新运行')
        record = password_record(password)
        del password
        remote = '''import json,os,sys,tempfile
from pathlib import Path
data=json.load(sys.stdin)
root=Path(data['root'])
assert root.is_dir(), '归档目录不存在'
target=root/'admin-password.json'
if target.exists():
    backup=root/'admin-password.previous.json'
    backup.write_bytes(target.read_bytes())
    backup.chmod(0o600)
fd,name=tempfile.mkstemp(prefix='.admin-password-',dir=root)
try:
    with os.fdopen(fd,'w') as f: json.dump(data['record'],f)
    os.replace(name,target)
finally:
    if os.path.exists(name): os.unlink(name)
print('管理员密码已设置。旧登录会话已失效。')
'''
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', args.ssh,
                                 'python3 -c ' + shlex.quote(remote)],
                                input=json.dumps({'root': args.root, 'record': record}),
                                capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('服务器写入失败，请检查 SSH 连接和目录权限')
        print(result.stdout.strip())
    except (ValueError, RuntimeError, OSError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) or '已取消')
        return 1
    finally:
        if args.keep_open:
            input('按回车关闭窗口。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
