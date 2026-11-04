"""凭据池：每个已认证用户名同时只租给一个观战连接。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import time

from .spectator import CredentialError


@dataclass(eq=False)
class Account:
    credentials: object = field(repr=False)
    identity: tuple | None = field(default=None, repr=False)
    checking: asyncio.Task | None = field(default=None, repr=False)
    checked_at: float = 0
    retry_at: float = 0
    busy: bool = False
    failed: bool = False


class AccountPool:
    def __init__(self, credentials, *, retry_seconds=30, refresh_seconds=1):
        self.accounts = [Account(source) for source in credentials]
        if not self.accounts:
            raise ValueError('至少需要一个观战凭据')
        self.retry_seconds = retry_seconds
        self.refresh_seconds = refresh_seconds
        self.changed = asyncio.Event()
        self.active_users = set()
        self.cursor = 0

    async def _check(self, account):
        try:
            account.identity = await account.credentials.get()
            account.failed = False
        except CredentialError:
            account.identity = None
            account.failed = True
            account.retry_at = time.monotonic() + self.retry_seconds
        finally:
            account.checked_at = time.monotonic()
            self.changed.set()

    def _start_checks(self):
        now = time.monotonic()
        for account in self.accounts:
            if account.busy or now < account.retry_at:
                continue
            if account.checking is None or (account.checking.done()
                    and now - account.checked_at >= self.refresh_seconds):
                account.checking = asyncio.create_task(self._check(account))

    async def _wait(self, ended):
        changed = asyncio.create_task(self.changed.wait())
        finished = asyncio.create_task(ended.wait())
        try:
            await asyncio.wait((changed, finished), timeout=self.refresh_seconds,
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            changed.cancel()
            finished.cancel()
            await asyncio.gather(changed, finished, return_exceptions=True)

    async def identities(self, ended):
        """排队前检查已知身份；慢认证或坏文件不阻塞其他可用账号。"""
        while not ended.is_set():
            self.changed.clear()
            self._start_checks()
            identities = [a.identity for a in self.accounts if a.identity is not None
                          and (a.busy or (a.checking is not None and a.checking.done()))]
            if identities:
                return identities
            await self._wait(ended)
        return []

    def invalidate(self, account):
        account.credentials.invalidate()
        account.identity = None
        account.failed = True
        account.retry_at = time.monotonic() + self.retry_seconds
        self.changed.set()

    @asynccontextmanager
    async def lease(self, ended):
        selected = None
        username = None
        try:
            while not ended.is_set():
                self.changed.clear()
                self._start_checks()
                for offset in range(len(self.accounts)):
                    index = (self.cursor + offset) % len(self.accounts)
                    account = self.accounts[index]
                    if (account.busy or account.checking is None or not account.checking.done()
                            or account.identity is None or time.monotonic() < account.retry_at):
                        continue
                    identity = account.identity
                    if identity[0] in self.active_users:
                        continue
                    # 从选择到登记之间不 await，同一用户名的多个文件也不会重复出租。
                    account.busy = True
                    self.active_users.add(identity[0])
                    selected, username = account, identity[0]
                    self.cursor = (index + 1) % len(self.accounts)
                    yield account, identity
                    return
                await self._wait(ended)
            yield None
        finally:
            if selected is not None:
                self.active_users.remove(username)
                selected.busy = False
                selected.checking = None
                self.changed.set()

    async def close(self):
        tasks = [a.checking for a in self.accounts if a.checking is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
