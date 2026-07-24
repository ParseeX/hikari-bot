"""统一管理机器人状态数据的 SQLite 持久化入口。"""

from functools import lru_cache
from pathlib import Path

from hikari_bot.core.constants import DATA_DIR

from .database import PersistenceError, StateDatabase, configure_sqlite_connection
from .migrations import initialize_state_store
from .repositories import StateRepository


@lru_cache(maxsize=1)
def get_state_store() -> StateRepository:
    """返回进程内唯一的状态仓储，并在首次使用时完成旧数据迁移。"""
    data_dir = Path(DATA_DIR)
    return initialize_state_store(data_dir / "hikari.db", data_dir)


def initialize_default_state_store() -> StateRepository:
    """在机器人启动阶段预热状态仓储，使迁移错误尽早暴露。"""
    return get_state_store()


__all__ = [
    "PersistenceError",
    "StateDatabase",
    "StateRepository",
    "configure_sqlite_connection",
    "get_state_store",
    "initialize_default_state_store",
    "initialize_state_store",
]
