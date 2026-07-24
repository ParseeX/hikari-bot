"""功能开关、白名单和 MyCard 状态的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .database import PersistenceError, StateDatabase


class StateRepository:
    """封装低频机器人状态的全部 SQL 读写操作。"""

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.initialize()

    def initialize(self) -> None:
        """创建当前版本所需的数据表和约束。"""
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feature_flags (
                        name TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
                    )
                    """
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS whitelist_groups (group_id TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS whitelist_users (user_id TEXT PRIMARY KEY)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mycard_bindings (
                        qq_id TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mycard_subscriptions (
                        username TEXT NOT NULL,
                        target_type TEXT NOT NULL CHECK (target_type IN ('group', 'private')),
                        target_id TEXT NOT NULL,
                        PRIMARY KEY (username, target_type, target_id)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise PersistenceError(f"初始化状态数据表失败：{exc}") from exc

    def get_flag(self, name: str, default: bool) -> bool:
        """读取开关；未设置时返回调用方给定的默认值。"""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM feature_flags WHERE name = ?", (name,)
            ).fetchone()
        return default if row is None else bool(row[0])

    def set_flag(self, name: str, enabled: bool) -> None:
        """新增或更新具名布尔开关。"""
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO feature_flags(name, enabled) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET enabled = excluded.enabled
                """,
                (name, int(enabled)),
            )

    def get_whitelist(self) -> dict[str, list[str]]:
        """返回与旧 JSON 完全兼容的群和用户白名单结构。"""
        with self.database.connect() as connection:
            groups = [
                row[0]
                for row in connection.execute(
                    "SELECT group_id FROM whitelist_groups ORDER BY group_id"
                )
            ]
            users = [
                row[0]
                for row in connection.execute(
                    "SELECT user_id FROM whitelist_users ORDER BY user_id"
                )
            ]
        return {"groups": groups, "users": users}

    def replace_whitelist(self, groups: list[str], users: list[str]) -> None:
        """用给定内容整体替换白名单，供兼容旧保存接口和首次迁移使用。"""
        with self.database.transaction() as connection:
            self._replace_whitelist(connection, groups, users)

    def add_group(self, group_id: str) -> bool:
        """新增群白名单；已存在时返回 False。"""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO whitelist_groups(group_id) VALUES (?)",
                (str(group_id),),
            )
        return cursor.rowcount == 1

    def is_group_allowed(self, group_id: str) -> bool:
        """判断群号是否在白名单中。"""
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM whitelist_groups WHERE group_id = ?", (str(group_id),)
            ).fetchone() is not None

    def get_bindings(self) -> dict[str, str]:
        """读取 QQ 到 MyCard 用户名的绑定表。"""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT qq_id, username FROM mycard_bindings ORDER BY qq_id"
            ).fetchall()
        return {qq_id: username for qq_id, username in rows}

    def replace_bindings(self, bindings: dict[str, str]) -> None:
        """整体替换绑定表，供兼容旧保存接口和首次迁移使用。"""
        with self.database.transaction() as connection:
            self._replace_bindings(connection, bindings)

    def set_binding(self, qq_id: str, username: str) -> None:
        """新增或更新单个 QQ 的 MyCard 绑定。"""
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO mycard_bindings(qq_id, username, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(qq_id) DO UPDATE SET
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (
                    str(qq_id),
                    str(username),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )

    def get_subscriptions(self) -> dict[str, list[list[str]]]:
        """返回 MyCard 监控模块所需的订阅字典结构。"""
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT username, target_type, target_id
                FROM mycard_subscriptions
                ORDER BY username, target_type, target_id
                """
            ).fetchall()
        subscriptions: dict[str, list[list[str]]] = {}
        for username, target_type, target_id in rows:
            subscriptions.setdefault(username, []).append([target_type, target_id])
        return subscriptions

    def replace_subscriptions(self, subscriptions: dict[str, list[list[str]]]) -> None:
        """整体替换订阅表，供兼容旧保存接口和首次迁移使用。"""
        with self.database.transaction() as connection:
            self._replace_subscriptions(connection, subscriptions)

    def subscribe(self, target_type: str, target_id: str, username: str) -> bool:
        """新增订阅；订阅已存在时返回 False。"""
        self._validate_target_type(target_type)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO mycard_subscriptions(username, target_type, target_id)
                VALUES (?, ?, ?)
                """,
                (str(username), target_type, str(target_id)),
            )
        return cursor.rowcount == 1

    def unsubscribe(self, target_type: str, target_id: str, username: str) -> bool:
        """删除一个订阅；不存在时返回 False。"""
        self._validate_target_type(target_type)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM mycard_subscriptions
                WHERE username = ? AND target_type = ? AND target_id = ?
                """,
                (str(username), target_type, str(target_id)),
            )
        return cursor.rowcount == 1

    def unsubscribe_all(self, target_type: str, target_id: str) -> bool:
        """删除一个群或私聊目标的全部订阅。"""
        self._validate_target_type(target_type)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM mycard_subscriptions WHERE target_type = ? AND target_id = ?",
                (target_type, str(target_id)),
            )
        return cursor.rowcount > 0

    def has_state(self, connection: sqlite3.Connection) -> bool:
        """判断任一受管状态表是否已有记录。"""
        for table in (
            "feature_flags",
            "whitelist_groups",
            "whitelist_users",
            "mycard_bindings",
            "mycard_subscriptions",
        ):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return True
        return False

    def import_legacy_state(
        self,
        connection: sqlite3.Connection,
        *,
        flags: dict[str, bool],
        groups: list[str],
        users: list[str],
        bindings: dict[str, str],
        subscriptions: dict[str, list[list[str]]],
    ) -> None:
        """在调用方控制的单个事务中导入所有旧状态。"""
        connection.execute("DELETE FROM feature_flags")
        connection.executemany(
            "INSERT INTO feature_flags(name, enabled) VALUES (?, ?)",
            [(name, int(enabled)) for name, enabled in flags.items()],
        )
        self._replace_whitelist(connection, groups, users)
        self._replace_bindings(connection, bindings)
        self._replace_subscriptions(connection, subscriptions)

    @staticmethod
    def _validate_target_type(target_type: str) -> None:
        if target_type not in {"group", "private"}:
            raise PersistenceError(f"不支持的订阅目标类型：{target_type}")

    @staticmethod
    def _replace_whitelist(
        connection: sqlite3.Connection, groups: list[str], users: list[str]
    ) -> None:
        connection.execute("DELETE FROM whitelist_groups")
        connection.execute("DELETE FROM whitelist_users")
        connection.executemany(
            "INSERT INTO whitelist_groups(group_id) VALUES (?)",
            [(value,) for value in sorted(set(map(str, groups)))],
        )
        connection.executemany(
            "INSERT INTO whitelist_users(user_id) VALUES (?)",
            [(value,) for value in sorted(set(map(str, users)))],
        )

    @staticmethod
    def _replace_bindings(
        connection: sqlite3.Connection, bindings: dict[str, str]
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute("DELETE FROM mycard_bindings")
        connection.executemany(
            "INSERT INTO mycard_bindings(qq_id, username, updated_at) VALUES (?, ?, ?)",
            [(str(qq_id), str(username), now) for qq_id, username in bindings.items()],
        )

    def _replace_subscriptions(
        self, connection: sqlite3.Connection, subscriptions: dict[str, list[list[str]]]
    ) -> None:
        connection.execute("DELETE FROM mycard_subscriptions")
        connection.executemany(
            """
            INSERT INTO mycard_subscriptions(username, target_type, target_id)
            VALUES (?, ?, ?)
            """,
            self._subscription_rows(subscriptions),
        )

    def _subscription_rows(
        self, subscriptions: dict[str, list[list[str]]]
    ) -> list[tuple[str, str, str]]:
        rows: set[tuple[str, str, str]] = set()
        for username, targets in subscriptions.items():
            for target in targets:
                if not isinstance(target, list) or len(target) != 2:
                    raise PersistenceError("订阅记录必须是包含目标类型和目标编号的列表")
                target_type, target_id = target
                self._validate_target_type(str(target_type))
                rows.add((str(username), str(target_type), str(target_id)))
        return sorted(rows)
