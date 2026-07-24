"""统一管理机器人状态数据的 SQLite 持久化入口。"""

from .database import PersistenceError, StateDatabase, configure_sqlite_connection
from .migrations import initialize_state_store
from .repositories import StateRepository

__all__ = [
    "PersistenceError",
    "StateDatabase",
    "StateRepository",
    "configure_sqlite_connection",
    "initialize_state_store",
]
