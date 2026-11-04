"""在本机隐藏输入密码，经官方登录验证后更新仓库外的采集凭据。"""
import argparse
import asyncio
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hikari_bot.services.mycard_archive.spectator import Credentials, CredentialError


async def save_account(path, username, password, password_file=None):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_bytes() if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.setup-', suffix='.json')
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'username': username, 'password': password}, stream, ensure_ascii=False)
        # 验证密码必须实际登录，不能让旧 Token 的成功掩盖密码错误。
        identity = await Credentials(temporary).get()
        if identity[0] != username:
            raise CredentialError('认证返回的账号不匹配')
        if password_file:
            shared = Path(password_file).expanduser().resolve()
            if shared == path:
                raise CredentialError('共享密码文件不能与账号凭据文件相同')
            if shared.exists():
                if json.loads(shared.read_text(encoding='utf-8')).get('password') != password:
                    raise CredentialError('共享密码与输入不同，请先统一更新共享密码文件')
            else:
                shared.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(shared, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'password': password}, stream, ensure_ascii=False)
            saved = json.loads(temporary.read_text(encoding='utf-8'))
            saved.pop('password')
            saved['password_file'] = os.path.relpath(shared, path.parent)
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
        current = path.read_bytes() if path.exists() else None
        if current != original:
            raise CredentialError('原凭据文件已变更，请重试')
        if original is not None:
            backup = path.with_name(path.name + '.' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.bak')
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream: stream.write(original)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        return username
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('凭据录入失败，原凭据未替换') from None
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', action='append', required=True)
    parser.add_argument('--username', action='append', required=True)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--password-file', type=Path, help='各账号共用的仓库外密码 JSON 文件')
    args = parser.parse_args()
    if len(args.file) != len(args.username): parser.error('文件数与账号数必须相同')
    results = []
    def report():
        if args.report:
            args.report.write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')
    def save(path, username, password):
        try:
            asyncio.run(save_account(path, username, password, args.password_file))
        except CredentialError as exc:
            result = {'username': username, 'saved': False, 'message': str(exc)}
        else:
            result = {'username': username, 'saved': True, 'message': '登录验证成功，已保存'}
        results.append(result)
        report()
        return result
    if not args.gui:
        shared_password = getpass.getpass('共用密码（隐藏输入）：') if args.password_file else None
        for path, username in zip(args.file, args.username):
            result = save(path, username, shared_password or getpass.getpass(f'{username} 密码（隐藏输入）：'))
            print(f"{username}：{result['message']}")
        return
    import tkinter as tk
    root = tk.Tk()
    root.title('MyCard 采集账号 · 密码录入')
    root.resizable(False, False)
    frame = tk.Frame(root, padx=24, pady=18)
    frame.pack()
    tk.Label(frame, text='密码仅提交至 MyCard 官方登录接口。\n验证成功后保存到本机独立凭据文件；留空则保留原 Token。',
             justify='left').grid(row=0, column=0, columnspan=2, pady=(0, 16))
    inputs = []
    labels = ['所有账号共用密码'] if args.password_file else args.username
    for index, username in enumerate(labels, 1):
        tk.Label(frame, text=username).grid(row=index, column=0, sticky='w', pady=8)
        entry = tk.Entry(frame, show='●', width=34)
        entry.grid(row=index, column=1, padx=(12, 0))
        inputs.append(entry)
    status = tk.StringVar(value='凭据不会显示在聊天、命令行或日志中。')
    tk.Label(frame, textvariable=status, wraplength=460, justify='left').grid(
        row=len(inputs) + 1, column=0, columnspan=2, pady=14)
    def submit():
        button.config(state='disabled')
        messages = []
        entries = [inputs[0]] * len(args.file) if args.password_file else inputs
        passwords = [entry.get() for entry in entries]
        for path, username, password in zip(args.file, args.username, passwords):
            if not password: continue
            status.set(f'正在验证 {username}，请稍候……')
            root.update_idletasks()
            result = save(path, username, password)
            messages.append(f"{username}：{result['message']}")
        for entry in inputs: entry.delete(0, 'end')
        status.set('\n'.join(messages) or '没有输入密码，原凭据保持不变。')
        button.config(state='normal')
    button = tk.Button(frame, text='验证并保存', command=submit)
    button.grid(row=len(inputs) + 2, column=0, pady=8)
    tk.Button(frame, text='关闭', command=root.destroy).grid(row=len(inputs) + 2, column=1)
    root.lift()
    inputs[0].focus_set()
    root.mainloop()
    report()


if __name__ == '__main__':
    main()
