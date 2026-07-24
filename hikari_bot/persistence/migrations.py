"""状态数据库的版本记录与旧 JSON 一次性迁移。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import PersistenceError, StateDatabase
from .repositories import StateRepository


SCHEMA_VERSION = "001_initial_state_schema"
LEGACY_IMPORT_VERSION = "002_legacy_json_import"


def initialize_state_store(
    database_path: str | Path, legacy_dir: str | Path
) -> StateRepository:
    """初始化状态库，并在首次启动时安全导入旧 JSON 数据。"""
    database = StateDatabase(database_path)
    repository = StateRepository(database)
    legacy_path = Path(legacy_dir)

    _ensure_migration_table(database)
    _record_schema_version(database)
    if _has_migration(database, LEGACY_IMPORT_VERSION):
        _backup_remaining_legacy_files(legacy_path)
        return repository

    legacy_state, originals = _read_legacy_state(legacy_path)
    renamed_files = _backup_originals(originals)
    try:
        with database.transaction() as connection:
            if not repository.has_state(connection):
                repository.import_legacy_state(connection, **legacy_state)
            _record_migration(connection, LEGACY_IMPORT_VERSION)
    except Exception:
        _restore_originals(renamed_files)
        raise
    return repository


def _ensure_migration_table(database: StateDatabase) -> None:
    with database.transaction() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )


def _record_schema_version(database: StateDatabase) -> None:
    with database.transaction() as connection:
        _record_migration(connection, SCHEMA_VERSION)


def _record_migration(connection: sqlite3.Connection, version: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (version, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def _has_migration(database: StateDatabase, version: str) -> bool:
    with database.connect() as connection:
        return connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone() is not None


def _read_legacy_state(legacy_dir: Path) -> tuple[dict[str, Any], list[Path]]:
    paths = {
        "flags": legacy_dir / "feature_flags.json",
        "whitelist": legacy_dir / "whitelist.json",
        "bindings": legacy_dir / "mycard_user.json",
        "subscriptions": legacy_dir / "subscribe.json",
    }
    values: dict[str, Any] = {}
    originals: list[Path] = []
    for name, path in paths.items():
        source, is_original = _select_legacy_source(path)
        if source is None:
            values[name] = None
            continue
        values[name] = _load_json(source)
        if is_original:
            originals.append(path)

    flags = _validate_flags(values["flags"])
    groups, users = _validate_whitelist(values["whitelist"])
    bindings = _validate_bindings(values["bindings"])
    subscriptions = _validate_subscriptions(values["subscriptions"])
    return (
        {
            "flags": flags,
            "groups": groups,
            "users": users,
            "bindings": bindings,
            "subscriptions": subscriptions,
        },
        originals,
    )


def _select_legacy_source(path: Path) -> tuple[Path | None, bool]:
    backup = path.with_name(f"{path.name}.bak")
    if path.exists() and backup.exists():
        raise PersistenceError(f"旧数据与备份同时存在，无法安全迁移：{path.name}")
    if path.exists():
        return path, True
    if backup.exists():
        return backup, False
    return None, False


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise PersistenceError(f"读取旧状态文件失败：{path.name}：{exc}") from exc


def _validate_flags(value: Any) -> dict[str, bool]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(enabled, bool)
        for name, enabled in value.items()
    ):
        raise PersistenceError("feature_flags.json 必须是字符串键和布尔值组成的对象")
    return value


def _validate_whitelist(value: Any) -> tuple[list[str], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, dict):
        raise PersistenceError("whitelist.json 必须是对象")
    groups = value.get("groups", [])
    users = value.get("users", [])
    if not isinstance(groups, list) or not isinstance(users, list):
        raise PersistenceError("白名单的 groups 和 users 必须是列表")
    if not all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in groups + users):
        raise PersistenceError("白名单编号必须是字符串或整数")
    return [str(item) for item in groups], [str(item) for item in users]


def _validate_bindings(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(qq_id, str) and isinstance(username, str)
        for qq_id, username in value.items()
    ):
        raise PersistenceError("mycard_user.json 必须是字符串 QQ 与用户名组成的对象")
    return value


def _validate_subscriptions(value: Any) -> dict[str, list[list[str]]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PersistenceError("subscribe.json 必须是对象")
    result: dict[str, list[list[str]]] = {}
    for username, targets in value.items():
        if not isinstance(username, str) or not isinstance(targets, list):
            raise PersistenceError("订阅用户名必须是字符串，订阅目标必须是列表")
        validated_targets: list[list[str]] = []
        for target in targets:
            if (
                not isinstance(target, list)
                or len(target) != 2
                or not all(isinstance(item, str) for item in target)
                or target[0] not in {"group", "private"}
            ):
                raise PersistenceError("订阅记录必须是 [group/private, 目标编号] 字符串列表")
            validated_targets.append(target)
        result[username] = validated_targets
    return result


def _backup_originals(originals: list[Path]) -> list[tuple[Path, Path]]:
    renamed: list[tuple[Path, Path]] = []
    try:
        for original in originals:
            backup = original.with_name(f"{original.name}.bak")
            if backup.exists():
                raise PersistenceError(f"旧状态备份已存在，拒绝覆盖：{backup.name}")
            original.replace(backup)
            renamed.append((original, backup))
    except (OSError, PersistenceError) as exc:
        _restore_originals(renamed)
        raise PersistenceError(f"创建旧状态备份失败：{exc}") from exc
    return renamed


def _restore_originals(renamed: list[tuple[Path, Path]]) -> None:
    for original, backup in reversed(renamed):
        if backup.exists() and not original.exists():
            backup.replace(original)


def _backup_remaining_legacy_files(legacy_dir: Path) -> None:
    originals = [
        legacy_dir / name
        for name in (
            "feature_flags.json",
            "whitelist.json",
            "mycard_user.json",
            "subscribe.json",
        )
        if (legacy_dir / name).exists()
    ]
    _backup_originals(originals)
