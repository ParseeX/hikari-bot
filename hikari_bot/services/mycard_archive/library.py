"""按系列集合汇总玩家卡组版本；原始对局归档保持独立。"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from hikari_bot.core.config import settings
from hikari_bot.services.card_catalog import CardCatalog

from .presentation import classify, consolidated_decks, read_rules
from .replay import Deck


@dataclass(frozen=True)
class DeckVersion:
    deck: Deck
    first_seen: float
    updated_at: float
    observations: int


class _ClassificationCatalog:
    """单次查询内复用卡号身份，避免每条旧观测重复读取整份规则的卡片。"""
    def __init__(self):
        self.catalog = CardCatalog(settings.card_catalog_path)
        self.identities = {}

    def canonical_codes(self, codes):
        missing = set(codes) - self.identities.keys()
        if missing:
            self.identities.update(self.catalog.canonical_codes(missing))
        return {code: self.identities[code] for code in codes}


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

    def _rules(self):
        try:
            return read_rules(self.path.with_name('deck-rules.json'))
        except FileNotFoundError:
            return []

    def _versions(self, rows, *, rules=None, catalog=None):
        if rules is None:
            rules = self._rules()
        if catalog is None:
            catalog = _ClassificationCatalog()
        # 系列按集合比较，命中张数和显示顺序不影响身份；未分类仍按主卡区分。
        versions = {}
        keys = {}
        for row in rows:
            deck = Deck(row['player'], tuple(sorted(json.loads(row['main']))),
                        tuple(sorted(json.loads(row['extra']))), tuple(sorted(json.loads(row['side']))))
            if deck.main not in keys:
                labels = frozenset(classify(deck, rules, catalog=catalog)['labels'])
                keys[deck.main] = ('series', labels) if labels else ('main', deck.main)
            key = keys[deck.main]
            previous = versions.pop(key, None)
            observations = row['observations'] if 'observations' in row.keys() else 1
            if previous:
                # 微调后的构筑整体采用新观测，不能把旧主卡的 Side 混入新构筑。
                if previous.deck.main == deck.main:
                    side = tuple(sorted((Counter(previous.deck.side) | Counter(deck.side)).elements()))
                    if len(side) > 15:
                        side = deck.side
                    deck = Deck(deck.player, deck.main, deck.extra, side)
                version = DeckVersion(deck, previous.first_seen, row['updated_at'],
                                      previous.observations + observations)
            else:
                version = DeckVersion(deck, row['observed_at'], row['updated_at'], observations)
            versions[key] = version
        return list(versions.values())

    def history(self, player):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM deck_observations WHERE player=? ORDER BY observed_at,match_id',
                              (player,)).fetchall()
        return self._versions(rows)

    def card_ids(self):
        """返回历史卡组全部卡号，最近观测的卡片优先预缓存。"""
        cards = {}
        with self.connect() as db:
            for row in db.execute('SELECT main,extra,side FROM deck_observations ORDER BY updated_at DESC'):
                for section in row:
                    for code in json.loads(section):
                        if isinstance(code, int) and code > 0:
                            cards[code] = None
        from .manual_decks import ManualDecks
        for entry in ManualDecks(self.path.with_name('manual-decks.json')).read()[0]:
            for section in ('main', 'extra', 'side'):
                cards.update(dict.fromkeys(entry[section]))
        return list(cards)

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
        from .manual_decks import ManualDecks
        for entry in ManualDecks(self.path.with_name('manual-decks.json')).read()[0]:
            player = entry['player']
            if player not in players:
                continue
            for saved in [*entry.get('history', []), entry]:
                if saved['player'] == player:
                    grouped[player].append({'player': player, 'observed_at': saved['updated_at'],
                                            'updated_at': saved['updated_at'], 'observations': 0,
                                            **{key: json.dumps(saved[key]) for key in ('main', 'extra', 'side')}})
        # 按实际使用时间排序，延迟归档的旧对局不会覆盖较新的手动上传。
        rules = self._rules()
        catalog = _ClassificationCatalog()
        return {player: self._versions(sorted(observations, key=lambda row: row['observed_at']),
                                       rules=rules, catalog=catalog)[-1]
                for player, observations in grouped.items()}
