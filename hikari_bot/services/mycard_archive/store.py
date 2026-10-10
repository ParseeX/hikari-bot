"""独立 SQLite 索引、原始文件与持久化私聊发件箱。"""
import hashlib
import json
import re
import sqlite3
import time
import struct
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
import zipfile

from .presentation import build_messages, classify, consolidated_decks, read_rules
from .replay import Deck, parse_replay


def atomic_write(path: Path, data: bytes):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ArchiveStore:
    def __init__(self, root: Path, recipients: set[str] | frozenset[str]):
        self.root = root
        self.recipients = sorted(recipients)
        self.root.mkdir(parents=True, exist_ok=True)
        self.rules_path = root / "deck-rules.json"
        if not self.rules_path.exists():
            atomic_write(self.rules_path, b'{"version": 1, "rules": []}\n')
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, room_id TEXT NOT NULL UNIQUE,
                    players TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    started REAL NOT NULL, finished REAL, issues TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS replays (
                    match_id TEXT NOT NULL REFERENCES matches(id), digest TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY(match_id, digest)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id INTEGER PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                    recipient TEXT NOT NULL, part INTEGER NOT NULL, message TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0,
                    UNIQUE(match_id, recipient, part)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "archive.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def begin(self, room_id: str, players: list[str]) -> tuple[str, bool]:
        match_id = hashlib.sha256(room_id.encode("utf-8")).hexdigest()[:24]
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO matches(id,room_id,players,started) VALUES(?,?,?,?)",
                       (match_id, room_id, json.dumps(players, ensure_ascii=False), time.time()))
            row = db.execute("SELECT status FROM matches WHERE id=?", (match_id,)).fetchone()
        (self.root / match_id).mkdir(exist_ok=True)
        return match_id, row["status"] == "active"

    def active(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM matches WHERE status='active'")]

    def issue(self, match_id: str, message: str):
        with self.connect() as db:
            row = db.execute("SELECT issues FROM matches WHERE id=?", (match_id,)).fetchone()
            issues = json.loads(row["issues"])
            if message not in issues:
                issues.append(message)
                db.execute("UPDATE matches SET issues=? WHERE id=?", (json.dumps(issues[:20], ensure_ascii=False), match_id))

    def replays(self, match_id: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT metadata FROM replays WHERE match_id=? ORDER BY ordinal", (match_id,))]

    def save_replay(self, match_id: str, data: bytes):
        digest = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM replays WHERE match_id=? AND digest=?", (match_id, digest)).fetchone():
                return
            row = db.execute("SELECT players FROM matches WHERE id=?", (match_id,)).fetchone()
            if row is None:
                raise ValueError("未知对局")
            ordinal = db.execute("SELECT count(*) FROM replays WHERE match_id=?", (match_id,)).fetchone()[0] + 1
        directory = self.root / match_id
        filename = f"{digest}.yrp"
        # 即使当前解析器不支持该格式，也保留服务端原始录像。
        atomic_write(directory / filename, data)
        metadata = {"sha256": digest, "file": filename, "bytes": len(data), "decks": [],
                    "start_time": struct.unpack_from("<I", data, 20)[0] if len(data) >= 24 else 0}
        try:
            decks = parse_replay(data)
            if Counter(d.player for d in decks) != Counter(json.loads(row["players"])):
                raise ValueError("录像玩家与房间不符")
            for index, deck in enumerate(decks, 1):
                ydk = f"{digest}-player-{index}.ydk"
                atomic_write(directory / ydk, deck.ydk().encode("utf-8"))
                metadata["decks"].append({**asdict(deck), "file": ydk, "side_known": False})
        except ValueError:
            self.issue(match_id, f"录像 {ordinal} 无法解析或玩家不匹配，已保留原文件")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO replays VALUES(?,?,?,?)",
                       (match_id, digest, ordinal, json.dumps(metadata, ensure_ascii=False)))

    def recover_files(self, match_id: str):
        """恢复文件已落盘、索引事务尚未提交的进程中断。"""
        for path in sorted((self.root / match_id).glob("*.yrp"), key=lambda p: p.stat().st_mtime):
            self.save_replay(match_id, path.read_bytes())

    def finalize(self, match_id: str, complete: bool):
        with self.connect() as db:
            row = db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if row["status"] != "active":
            return
        replays = self.replays(match_id)
        if not replays:
            complete = False
            self.issue(match_id, "未收到录像，不能提取卡组")
        elif any(not replay["decks"] for replay in replays):
            complete = False
        try:
            rules = read_rules(self.rules_path)
        except (OSError, ValueError):
            rules = []
            self.issue(match_id, "识别规则读取失败，本场仅提供卡组链接和原始文件")
        with self.connect() as db:
            issues = json.loads(db.execute("SELECT issues FROM matches WHERE id=?", (match_id,)).fetchone()[0])
        players = json.loads(row["players"])
        messages = build_messages(match_id, players, replays, issues, complete, rules)
        for replay in replays:
            for raw in replay["decks"]:
                raw["classification"] = classify(Deck(raw["player"], tuple(raw["main"]), tuple(raw["extra"])), rules)
        combined = []
        for index, deck in enumerate(consolidated_decks(replays), 1):
            filename = f"combined-player-{index}.ydk"
            atomic_write(self.root / match_id / filename, deck.ydk().encode("utf-8"))
            combined.append({**asdict(deck), "file": filename, "side_known": False,
                             "side_source": "observed_substitutions", "classification": classify(deck, rules)})
        finished = time.time()
        manifest = {"id": match_id, "room_id": row["room_id"], "players": players,
                    "started": row["started"], "finished": finished, "complete": complete,
                    "issues": issues, "replays": replays, "combined_decks": combined}
        atomic_write(self.root / match_id / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        with self.connect() as db:
            db.execute("UPDATE matches SET status=?,finished=? WHERE id=?",
                       ("complete" if complete else "partial", finished, match_id))
            for recipient in self.recipients:
                for part, message in enumerate(messages):
                    db.execute("INSERT OR IGNORE INTO deliveries(match_id,recipient,part,message) VALUES(?,?,?,?)",
                               (match_id, recipient, part, message))

    def pending(self) -> list[dict]:
        with self.connect() as db:
            # 前一段失败时不能先发同一收件人的后续段；不阻塞其他收件人。
            return [dict(row) for row in db.execute("""
                SELECT d.* FROM deliveries d WHERE d.sent=0 AND d.retry_at<=?
                AND NOT EXISTS(SELECT 1 FROM deliveries previous
                    WHERE previous.match_id=d.match_id AND previous.recipient=d.recipient
                    AND previous.part<d.part AND previous.sent=0)
                ORDER BY d.id LIMIT 100
            """, (time.time(),))]

    def delivered(self, delivery_id: int, success: bool):
        with self.connect() as db:
            row = db.execute("SELECT attempts FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            attempts = row[0] + 1
            db.execute("UPDATE deliveries SET sent=?,attempts=?,retry_at=? WHERE id=?",
                       (int(success), attempts, time.time() + min(3600, 5 * 2**min(attempts, 10)), delivery_id))

    def stats(self) -> dict:
        with self.connect() as db:
            result = dict(db.execute("SELECT status,count(*) FROM matches GROUP BY status"))
            result["pending_messages"] = db.execute("SELECT count(*) FROM deliveries WHERE sent=0").fetchone()[0]
            return result

    def bundle(self, match_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{24}", match_id):
            raise ValueError("归档编号格式不正确")
        directory = self.root / match_id
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("归档不存在或尚未结束")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = ["manifest.json"]
        names.extend(deck["file"] for deck in manifest.get("combined_decks", []))
        for replay in manifest["replays"]:
            names.append(replay["file"])
            names.extend(deck["file"] for deck in replay["decks"])
        output = directory / "archive.zip"
        temporary = directory / (uuid4().hex + ".zip.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in names:
                    if Path(name).name != name:
                        raise ValueError("归档路径异常")
                    archive.write(directory / name, name)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return output
