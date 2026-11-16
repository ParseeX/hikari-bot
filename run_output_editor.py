"""启动独立文案后台；只绑定回环地址，由 Caddy 提供 HTTPS。"""
import os
import sys
from pathlib import Path
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == '__main__':
    os.umask(0o077)
    uvicorn.run('hikari_bot.qq_official.editor_web:create_app', factory=True,
                host='127.0.0.1', port=8792, proxy_headers=True,
                forwarded_allow_ips='127.0.0.1', access_log=False)
