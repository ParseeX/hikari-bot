"""卡片资料查询使用只读快照；手动别名通过独立事务保存。"""
from collections import Counter
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
import random
import re
import sqlite3
import unicodedata


def normalize(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


class AmbiguousCardName(ValueError):
    """一个手动别名对应多张卡，由命令入口展示全部候选。"""

    def __init__(self, cards):
        self.cards = tuple(cards)
        lines = ['这个别名对应多张卡，请使用卡密重新查询：']
        for card in self.cards:
            name = card.get('cn_name') or card.get('jp_name') or card.get('en_name') or '未命名卡片'
            code = card.get('passcode') or str(card['id'])
            lines.append(f'{name}：{code}')
        super().__init__('\n'.join(lines))


class CardCatalog:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        # mode=ro 防止路径写错时生成一个空库，也不允许查询修改资料。
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    def validate(self):
        with self.connect() as db:
            version = db.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
            if not version or version[0] not in {'1', '2', '3', '4'}:
                raise ValueError('不支持的卡片主库版本')
            if not db.execute('SELECT 1 FROM cards LIMIT 1').fetchone():
                raise ValueError('卡片主库为空，请先执行 sync-base')
            db.execute('SELECT image_id FROM card_artwork_ids LIMIT 1')

    @staticmethod
    def _by_id(db, value):
        text = str(value).strip()
        if not text.isascii() or not text.isdigit():
            return None
        text = text.lstrip('0')
        # SQLite 整数参数有上限；错误的长卡密也应返回未找到。
        if not text or len(text) > 19 or int(text) > 2**63 - 1:
            return None
        kind = 'temporary' if int(text) >= 100000000 else 'passcode'
        key = str(int(text)) if kind == 'temporary' else f'{int(text):08d}'
        row = db.execute('SELECT c.* FROM cards c JOIN card_identifiers i ON i.card_id=c.id '
                         'WHERE i.kind=? AND i.value=?', (kind, key)).fetchone()
        if row is None:
            row = db.execute('SELECT c.* FROM cards c JOIN card_artwork_ids a ON a.card_id=c.id '
                             'WHERE a.image_id=?', (int(text),)).fetchone()
        return row

    @staticmethod
    def _info(db, row):
        if row is None:
            return None
        info = {'id': int(row['passcode'] or row['temporary_id'] or 0),
                'cid': row['konami_cid'], 'catalog_id': row['id'],
                'passcode': row['passcode'], 'temporary_id': row['temporary_id'],
                'jp_ruby': row['japanese_reading'] or ''}
        for name in db.execute("SELECT source,name FROM card_names WHERE card_id=? ORDER BY source LIKE 'ygocdb:%'", (row['id'],)):
            if name['source'].startswith('ygocdb:') or name['source'] in {'jhs:cn_name', 'jhs:jp_name'}:
                info[name['source'].split(':', 1)[1]] = name['name']
        text = db.execute("SELECT * FROM card_texts WHERE card_id=? AND language='zh' "
                          "ORDER BY source='ygocdb' DESC,source LIMIT 1", (row['id'],)).fetchone()
        info['text'] = {'types': text['type_text'] if text else '',
                        'desc': text['effect'] if text else '',
                        'pdesc': text['pendulum_effect'] if text else ''}
        fields = {'type': 'type_code', 'race': 'race_code', 'attribute': 'attribute_code',
                  'atk': 'atk_raw', 'def': 'def_raw', 'level': 'level_raw', 'ot': 'scope_code'}
        info['data'] = {key: row[column] for key, column in fields.items()}
        info['data']['setcode'] = int(row['setcode'] or 0)
        return info

    def by_id(self, value):
        with self.connect() as db:
            return self._info(db, self._by_id(db, value))

    def alias_targets(self, keyword):
        """只读取完整手动别名的目标，普通卡名片段不触发选择菜单。"""
        with self.connect() as db:
            rows = db.execute('SELECT DISTINCT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                              "WHERE n.source='manual:alias' AND n.normalized=? "
                              'ORDER BY c.konami_cid IS NULL,c.konami_cid,c.id', (normalize(keyword),)).fetchall()
            return [self._info(db, row) for row in rows]

    def add_alias(self, code, alias):
        """只用精确卡密绑定别名；保留其他卡的同名别名，重复添加不产生副本。"""
        name = unicodedata.normalize('NFKC', alias).strip()
        key = normalize(name)
        if not key or len(key) > 128 or not any(c.isalpha() for c in key):
            raise ValueError('别名须包含文字，不能是纯数字，长度不能超过 128 字。')
        if any(unicodedata.category(c).startswith('C') for c in name):
            raise ValueError('别名不能包含控制字符。')
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = self._by_id(db, normalize(str(code)))
                if row is None:
                    raise ValueError('未找到这个卡密，请使用准确的卡密添加别名。')
                existing = db.execute("SELECT 1 FROM card_names WHERE card_id=? "
                                      "AND source='manual:alias' AND normalized=?", (row['id'], key)).fetchone()
                if not existing:
                    db.execute('INSERT INTO card_names VALUES (?,?,?,?,?)',
                               (row['id'], 'und', 'manual:alias', name, key))
                info = self._info(db, row)
            return info, not bool(existing)
        finally:
            db.close()

    def cards_by_id(self, codes):
        """批量读取卡片名称和统一身份，使用同一个只读快照。"""
        with self.connect() as db:
            return {code: self._info(db, self._by_id(db, code)) for code in set(codes)}

    def canonical_codes(self, codes):
        """仅按主库显式身份关系归一化异画、临时编号；未知卡号保持原值。"""
        result = {}
        with self.connect() as db:
            for code in set(codes):
                row = self._by_id(db, code)
                result[code] = int(row['passcode'] or row['temporary_id'] or code) if row else code
        return result

    def deck_sort_data(self, codes):
        """一次只读快照返回排序字段；异画使用原卡资料，但保留输入卡号。"""
        result = {}
        with self.connect() as db:
            for code in set(codes):
                row = self._by_id(db, code)
                if row is not None:
                    result[code] = tuple(row[field] for field in
                                         ('type_code', 'level_raw', 'atk_raw', 'def_raw'))
        return result

    def search(self, keyword: str, *, fuzzy=False):
        key = normalize(keyword)
        if not key:
            return None
        with self.connect() as db:
            if key.isascii() and key.isdigit():
                return self._info(db, self._by_id(db, key))
            aliases = db.execute('SELECT DISTINCT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                                 "WHERE n.source='manual:alias' AND n.normalized=? "
                                 'ORDER BY c.konami_cid IS NULL,c.konami_cid,c.id', (key,)).fetchall()
            if len(aliases) > 1:
                raise AmbiguousCardName([self._info(db, row) for row in aliases])
            if aliases:
                return self._info(db, aliases[0])
            # 精确别名优先；模糊查询仅搜索卡名，百分号和下划线都是普通文字。
            row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                             'WHERE n.normalized=? ORDER BY c.konami_cid IS NULL,c.konami_cid,c.id LIMIT 1', (key,)).fetchone()
            if row is None:
                row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                                 "WHERE n.source!='manual:alias' AND instr(n.normalized,?)>0 "
                                 'ORDER BY length(n.normalized),c.konami_cid IS NULL,c.konami_cid,c.id LIMIT 1', (key,)).fetchone()
            if row is None and fuzzy:
                row = self._closest_name(db, key)
            return self._info(db, row)

    def search_many(self, keyword: str):
        """卡名按片段、手动别名按完整名称查询；按身份去重，精确名称优先。"""
        key = normalize(keyword)
        if not key:
            return []
        with self.connect() as db:
            if key.isascii() and key.isdigit():
                card = self._info(db, self._by_id(db, key))
                return [card] if card else []
            rows = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                              "WHERE ((n.source='manual:alias' AND n.normalized=?) "
                              "OR (n.source!='manual:alias' AND instr(n.normalized,?)>0)) "
                              'AND (c.passcode IS NOT NULL OR c.temporary_id IS NOT NULL) '
                              'GROUP BY c.id ORDER BY MAX(n.normalized=?) DESC,MIN(length(n.normalized)),'
                              'c.konami_cid IS NULL,c.konami_cid,c.id', (key, key, key)).fetchall()
            return [self._info(db, row) for row in rows]

    @staticmethod
    def _closest_name(db, key):
        """综合连续字序和共有字符排序，始终取第一名，不设相似度门槛。"""
        query = ''.join(c for c in key if c.isalnum())
        if not query or query.isdigit() or len(query) > 128:
            return None
        query_counts = Counter(query)
        best = None
        best_id = None
        numbers = re.findall(r'\d+', query)
        rows = db.execute('SELECT DISTINCT n.card_id,n.normalized,c.konami_cid '
                          'FROM card_names n JOIN cards c ON c.id=n.card_id '
                          "WHERE n.source!='manual:alias' "
                          'AND (c.passcode IS NOT NULL OR c.temporary_id IS NOT NULL)')
        for row in rows:
            name = ''.join(c for c in row['normalized'] if c.isalnum())
            if not name:
                continue
            if query == name:
                score = (3, 1.)
            elif query in name:
                score = (2, len(query) / len(name))
            else:
                # No. 编号等数字具有卡片身份含义，不能当作错字改成另一张卡。
                if re.findall(r'\d+', name) != numbers:
                    continue
                common = sum(min(count, query_counts[char]) for char, count in Counter(name).items())
                overlap = 2 * common / (len(query) + len(name))
                # 共有字符是字序得分的上界，只跳过不可能成为第一名的候选。
                if best is not None and (1, overlap) < best[:2]:
                    continue
                ordered = SequenceMatcher(None, query, name, autojunk=False).ratio()
                # 降低纯字序变化的惩罚，同时让字序也一致的名字排在前面。
                score = (1, max(ordered, .9 * overlap))
            rank = (*score, -(row['konami_cid'] is None), -(row['konami_cid'] or 0), -row['card_id'])
            if best is None or rank > best:
                best, best_id = rank, row['card_id']
        return db.execute('SELECT * FROM cards WHERE id=?', (best_id,)).fetchone() if best_id is not None else None

    def random_id(self, seed=None, *, include_artworks=False):
        with self.connect() as db:
            rows = db.execute('SELECT COALESCE(passcode,temporary_id) FROM cards '
                              'WHERE passcode IS NOT NULL OR temporary_id IS NOT NULL '
                              'ORDER BY konami_cid').fetchall()
            if not rows:
                raise ValueError('卡片主库没有可用卡密')
            ids = [int(row[0]) for row in rows]
            if include_artworks:
                ids.extend(row[0] for row in db.execute('SELECT image_id FROM card_artwork_ids ORDER BY image_id'))
            return random.Random(seed).choice(list(dict.fromkeys(ids)))

    def image_ids(self, keyword: str) -> list[int]:
        """原画在前，已登记异画按图片编号升序；异画编号输入也返回整组。"""
        info = self.search(keyword, fuzzy=True)
        if not info or not info['id']:
            return []
        with self.connect() as db:
            rows = db.execute('SELECT image_id FROM card_artwork_ids WHERE card_id=? ORDER BY image_id',
                              (info['catalog_id'],))
            return list(dict.fromkeys([info['id'], *(row[0] for row in rows)]))

    def metaltronus(self, value):
        with self.connect() as db:
            target = self._by_id(db, value)
            if target is None or not (target['type_code'] or 0) & 1:
                return []
            if not target['race_code'] or not target['attribute_code']:
                return []
            rows = db.execute(
                'SELECT COALESCE(passcode,temporary_id) FROM cards '
                'WHERE id!=? AND (type_code & 1)!=0 AND (type_code & 16384)=0 '
                'AND (passcode IS NOT NULL OR temporary_id IS NOT NULL) '
                'AND ((atk_raw=?)+(race_code=?)+(attribute_code=?))>=2 ORDER BY konami_cid',
                (target['id'], target['atk_raw'], target['race_code'], target['attribute_code']))
            return [int(row[0]) for row in rows]
