"""复用原解析插件的隔离工作进程，不在官方 Bot 内注册它的事件和定时任务。"""
from hikari_bot.services.wording import wording
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import sys
from tempfile import TemporaryDirectory

from .platform import Media, Result

MAX_BYTES = 100 * 1024 * 1024
MAX_PARTS = 40
WORKER = Path(__file__).with_name("link_worker.py")
LINK = re.compile(r"https?://|\bBV[A-Za-z0-9]{10}\b", re.I)


def load_result(directory):
    """只接受工作目录内的普通文件；绝不把返回路径直接交给消息适配器。"""
    directory = Path(directory).resolve()
    manifest = directory / "result.json"
    if manifest.stat().st_size > 128 * 1024:
        raise ValueError("解析结果过大")
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) > MAX_PARTS:
        raise ValueError("解析结果过多")
    parts, size = [], 0
    for row in rows:
        if row["kind"] == "text":
            value = row["text"]
            if not isinstance(value, str) or len(value) > 16000:
                raise ValueError("解析文字过长")
            parts.append(value)
            continue
        path = (directory / row["path"]).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise ValueError("解析媒体路径无效")
        size += path.stat().st_size
        if size > MAX_BYTES:
            raise ValueError("解析媒体总大小超限")
        parts.append(Media(row["kind"], path.read_bytes(), Path(row.get("name") or path.name).name))
    return Result(media=tuple(parts))


class LinkParser:
    def __init__(self, *, timeout=105):
        self.timeout = timeout
        self.lock = asyncio.Lock()

    async def parse(self, text):
        if len(text) > 16000:
            return Result(wording('links.link_parser_89428994', '链接消息过长。', {}))
        if self.lock.locked():
            return Result(wording('links.link_parser_ecde216f', '正在解析其他媒体，请稍后再试。', {}))
        async with self.lock:
            with TemporaryDirectory(prefix="hikari-parser-") as directory:
                root = Path(directory)
                (root / "request.json").write_text(json.dumps({"text": text}), encoding="utf-8")
                # 不继承 Bot 凭据、代理密码、旧插件 Cookie 或原配置目录。
                env = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC"}}
                env.update(HOME=directory, USERPROFILE=directory, TEMP=directory, TMP=directory,
                           XDG_CACHE_HOME=directory, XDG_CONFIG_HOME=directory, XDG_DATA_HOME=directory)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", "-B", str(WORKER), directory,
                    cwd=directory, env=env, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != "nt")
                try:
                    await asyncio.wait_for(process.wait(), self.timeout)
                    if process.returncode != 0:
                        return Result(wording('links.link_parser_abcdf26a', '链接解析或下载失败；未自动重试。需要登录的平台须另行配置，旧 Cookie 未迁移。', {}))
                    return await asyncio.to_thread(load_result, root)
                except TimeoutError:
                    return Result(wording('links.link_parser_1479679f', '链接解析超时；未自动重试，请稍后再试或直接打开原链接。', {}))
                finally:
                    if process.returncode is None:
                        if os.name == "nt":
                            killer = await asyncio.create_subprocess_exec(
                                "taskkill", "/PID", str(process.pid), "/T", "/F",
                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                            await killer.wait()
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        await process.wait()
