"""Bot 的卡片资料入口；每次查询打开只读连接，及时看到主库更新。"""
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
import random
import sqlite3
import unicodedata


def normalize(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()


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
        if not text.isascii() or not text.isdigit() or int(text) <= 0:
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
            # 精确别名优先；模糊查询仅搜索卡名，百分号和下划线都是普通文字。
            row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                             'WHERE n.normalized=? ORDER BY c.konami_cid IS NULL,c.konami_cid,c.id LIMIT 1', (key,)).fetchone()
            if row is None:
                row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                                 'WHERE instr(n.normalized,?)>0 '
                                 'ORDER BY length(n.normalized),c.konami_cid IS NULL,c.konami_cid,c.id LIMIT 1', (key,)).fetchone()
            if row is None and fuzzy:
                row = self._closest_name(db, key)
            return self._info(db, row)

    def search_many(self, keyword: str):
        """返回所有名称包含关键词的卡，按卡片身份去重，精确名称优先。"""
        key = normalize(keyword)
        if not key:
            return []
        with self.connect() as db:
            if key.isascii() and key.isdigit():
                card = self._info(db, self._by_id(db, key))
                return [card] if card else []
            rows = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                              'WHERE instr(n.normalized,?)>0 '
                              'AND (c.passcode IS NOT NULL OR c.temporary_id IS NOT NULL) '
                              'GROUP BY c.id ORDER BY MAX(n.normalized=?) DESC,MIN(length(n.normalized)),'
                              'c.konami_cid IS NULL,c.konami_cid,c.id', (key, key)).fetchall()
            return [self._info(db, row) for row in rows]

    @staticmethod
    def _closest_name(db, key):
        """补充标点差异、漏字和少量错字；始终返回一张已确定身份的卡。"""
        query = ''.join(c for c in key if c.isalnum())
        if not query or len(query) > 128:
            return None
        best, card_id = None, None
        rows = db.execute('SELECT DISTINCT n.card_id,n.normalized,c.konami_cid '
                          'FROM card_names n JOIN cards c ON c.id=n.card_id')
        for row in rows:
            name = ''.join(c for c in row['normalized'] if c.isalnum())
            if not name:
                continue
            if query in name:
                score = (2, len(query) / len(name))
            else:
                # 短输入只做包含匹配，避免一两个字的错字匹配到无关卡片。
                if len(query) < 3 or min(len(query), len(name)) / max(len(query), len(name)) < .6:
                    continue
                matcher = SequenceMatcher(None, query, name, autojunk=False)
                if matcher.quick_ratio() < .75:
                    continue
                similarity = matcher.ratio()
                if similarity < .75:
                    continue
                score = (1, similarity)
            # 同分时稳定排序，不依赖 SQL 返回顺序或同一张卡的别名数量。
            rank = (*score, -(row['konami_cid'] is None), -(row['konami_cid'] or 0), -row['card_id'])
            if best is None or rank > best:
                best, card_id = rank, row['card_id']
        return db.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone() if card_id else None

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
        info = self.search(keyword)
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
