"""玩家卡组快照与连续版本；原始对局归档保持独立。"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from .presentation import consolidated_decks
from .replay import Deck


@dataclass(frozen=True)
class DeckVersion:
    deck: Deck
    first_seen: float
    updated_at: float
    observations: int


class DeckLibrary:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def initialize(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS deck_observations (
                match_id TEXT NOT NULL REFERENCES matches(id),
                player TEXT NOT NULL,
                main TEXT NOT NULL, extra TEXT NOT NULL, side TEXT NOT NULL,
                observed_at REAL NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (match_id, player)
            );
            CREATE INDEX IF NOT EXISTS deck_observations_player_time
                ON deck_observations(player, observed_at, match_id);
            CREATE TABLE IF NOT EXISTS deck_library_index (
                match_id TEXT PRIMARY KEY REFERENCES matches(id)
            );
        """)

    @staticmethod
    def record_match(db, match_id, replays, started, finished):
        """与归档完成事务一起提交；重启或补录不会重复增加观测次数。"""
        if db.execute('SELECT 1 FROM deck_library_index WHERE match_id=?', (match_id,)).fetchone():
            return
        for deck in consolidated_decks(replays):
            times = [r.get('start_time', 0) for r in replays
                     if any(d['player'] == deck.player for d in r.get('decks', [])) and r.get('start_time', 0) > 0]
            observed_at = min(times) if times else started
            db.execute("""INSERT OR IGNORE INTO deck_observations
                (match_id,player,main,extra,side,observed_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
                       (match_id, deck.player, json.dumps(deck.main), json.dumps(deck.extra),
                        json.dumps(deck.side), observed_at, finished))
        db.execute('INSERT OR IGNORE INTO deck_library_index VALUES(?)', (match_id,))

    @contextmanager
    def connect(self):
        # 查询不会创建空数据库，关闭采集后仍可读取已有卡组库。
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _versions(rows):
        versions = []
        for row in rows:
            deck = Deck(row['player'], tuple(sorted(json.loads(row['main']))),
                        tuple(sorted(json.loads(row['extra']))), tuple(sorted(json.loads(row['side']))))
            previous = versions[-1] if versions else None
            if previous and (previous.deck.main, previous.deck.extra) == (deck.main, deck.extra):
                side = tuple(sorted((Counter(previous.deck.side) | Counter(deck.side)).elements()))
                versions[-1] = DeckVersion(Deck(deck.player, deck.main, deck.extra, side),
                                           previous.first_seen, max(previous.updated_at, row['updated_at']),
                                           previous.observations + 1)
            else:
                # A -> B -> A 是三个连续版本，不把首次 A 的 Side 带进后来重建的 A。
                versions.append(DeckVersion(deck, row['observed_at'], row['updated_at'], 1))
        return versions

    def history(self, player):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM deck_observations WHERE player=? ORDER BY observed_at,match_id',
                              (player,)).fetchall()
        return self._versions(rows)

    def latest(self, player):
        return self.latest_many([player]).get(player)

    def latest_many(self, players):
        if not players:
            return {}
        placeholders = ','.join('?' for _ in players)
        with self.connect() as db:
            rows = db.execute(f'SELECT * FROM deck_observations WHERE player IN ({placeholders}) '
                              'ORDER BY player,observed_at,match_id', tuple(players)).fetchall()
        grouped = defaultdict(list)
        for row in rows:
            grouped[row['player']].append(row)
        return {player: self._versions(observations)[-1] for player, observations in grouped.items()}
