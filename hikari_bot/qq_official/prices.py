"""Existing price semantics with isolated srbs sessions and version cache."""
from .output_templates import present
from .price_outputs import format_comparison, rarity_prompt
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import time
from hikari_bot.features.card_prices.client import JhsUnavailable
from hikari_bot.features.card_prices.models import CardVersion, normalized
from hikari_bot.features.card_prices.service import ComparisonService, group_rarities
from hikari_bot.features.card_prices.version_catalog import VersionCatalog

class IsolatedVersionCache(VersionCatalog):

    def __init__(self, path, catalog):
        super().__init__(path)
        self.catalog = catalog
        if self.path.resolve() == Path(catalog.path).resolve():
            raise ValueError('srbs cache must be separate from the card catalog')

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS versions (card_id INTEGER PRIMARY KEY, name TEXT, ids TEXT, payload TEXT, checked REAL)')
        return db

    def source_identity(self, card_id, name_jp):
        with self.catalog.connect() as db:
            if not db.execute("SELECT 1 FROM card_names WHERE card_id=? AND language='ja' AND normalized=?", (card_id, normalized(name_jp))).fetchone():
                return None
            return self.identities(db, card_id)

    def load(self, card_id, name_jp):
        ids = self.source_identity(card_id, name_jp)
        if ids is None:
            return None
        db = self.connect()
        try:
            row = db.execute('SELECT name,ids,payload,checked FROM versions WHERE card_id=?', (card_id,)).fetchone()
            if row and row[0] == name_jp and (json.loads(row[1]) == ids):
                return (ids, [CardVersion.from_dict(v) for v in json.loads(row[2])], row[3])
            return (ids, None, None)
        finally:
            db.close()

    def save(self, card_id, name_jp, expected_ids, ids, payloads):
        if self.source_identity(card_id, name_jp) != expected_ids:
            raise ValueError('card identity changed during query')
        versions = [CardVersion.from_dict(r) for r in self.validate(payloads, ids, name_jp)]
        db = self.connect()
        try:
            with db:
                db.execute('INSERT OR REPLACE INTO versions VALUES (?,?,?,?,?)', (card_id, name_jp, json.dumps(expected_ids), json.dumps([asdict(v) for v in versions]), self.clock()))
            return versions
        finally:
            db.close()

class PriceQueries:

    def __init__(self, jhs, cardrush, cache, *, clock=time.monotonic):
        self.comparison = ComparisonService(jhs, cardrush, cache)
        self.clock = clock
        self.sessions = {}
        self.generations = {}

    def active(self, user_id):
        return user_id in self.sessions

    def cancel(self, user_id):
        self.sessions.pop(user_id, None)
        self.generations[user_id] = self.generations.get(user_id, 0) + 1

    async def start(self, info, user_id, *, rarity=None, prefix=None, japanese=False):
        self.sessions.pop(user_id, None)
        generation = self.generations.get(user_id, 0) + 1
        self.generations[user_id] = generation
        name = info.get('jp_name')
        if not name:
            return present('price.prices_1', {})
        aliases = tuple((str(info[k]) for k in ('cn_name', 'sc_name', 'md_name', 'nwbbs_n', 'cnocg_n') if info.get(k)))
        try:
            versions = await asyncio.wait_for(self.comparison.versions(name, rarity, prefix, aliases, catalog_id=info.get('catalog_id')), timeout=90)
        except (JhsUnavailable, TimeoutError):
            return present('price.prices_2', {})
        if self.generations.get(user_id) != generation:
            return present('price.prices_3', {})
        if not versions:
            return present('price.prices_4', {})
        groups = group_rarities(versions)
        self.sessions[user_id] = (self.clock() + 180, name, groups, japanese)
        return present('price.prompt',{'body':rarity_prompt(name, groups)})

    async def choose(self, value, user_id):
        state = self.sessions.get(user_id)
        if state is None:
            return present('price.prices_5', {})
        if value.casefold() in {'取消', '退出', 'cancel'}:
            self.sessions.pop(user_id, None)
            return present('price.prices_6', {})
        expires, name, groups, japanese = state
        if self.clock() > expires:
            self.sessions.pop(user_id, None)
            return present('price.prices_7', {})
        rarity = value.upper()
        if value.isascii() and value.isdigit():
            index = int(value) - 1
            rarity = list(groups)[index] if 0 <= index < len(groups) else ''
        if rarity not in groups:
            return present('price.prices_8', {'value1': f'{len(groups)}'})
        self.sessions.pop(user_id, None)
        try:
            rows = await asyncio.wait_for(self.comparison.compare(name, groups[rarity], japanese=japanese), timeout=90)
            return present('price.result',{'body':'\n\n'.join(format_comparison(name, rarity, rows, japanese=japanese))})
        except TimeoutError:
            return present('price.prices_9', {})
        except Exception:
            return present('price.prices_10', {})
