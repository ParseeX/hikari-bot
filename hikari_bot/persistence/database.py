"""SQLite 连接、事务与并发配置。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


class PersistenceError(RuntimeError):
    """持久化操作无法安全完成时抛出的异常。"""


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    """为短生命周期 SQLite 连接启用一致的完整性和并发策略。"""
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        # WAL 允许读操作与短写事务并发，适合命令和监控任务共用数据库。
        connection.execute("PRAGMA journal_mode = WAL")
        # 写入碰撞时等待五秒，避免直接向用户暴露 database is locked。
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:
        raise PersistenceError(f"配置 SQLite 连接失败：{exc}") from exc


class StateDatabase:
    """管理单个状态数据库的连接与显式事务。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        """打开数据库并应用所有共享 SQLite 配置。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5.0)
        except (OSError, sqlite3.Error) as exc:
            raise PersistenceError(f"打开状态数据库失败：{exc}") from exc

        try:
            configure_sqlite_connection(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """创建立即写事务，发生异常时完整回滚。"""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            try:
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PersistenceError(f"提交状态数据库事务失败：{exc}") from exc
        finally:
            connection.close()
