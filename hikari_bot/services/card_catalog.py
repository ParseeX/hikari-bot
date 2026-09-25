"""Bot 的卡片资料入口；每次查询打开只读连接，及时看到主库更新。"""
from contextlib import contextmanager
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
            if not version or version[0] not in {'1', '2', '3'}:
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
        for name in db.execute('SELECT source,name FROM card_names WHERE card_id=?', (row['id'],)):
            if name['source'].startswith('ygocdb:'):
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

    def search(self, keyword: str):
        key = normalize(keyword)
        if not key:
            return None
        with self.connect() as db:
            if key.isascii() and key.isdigit():
                return self._info(db, self._by_id(db, key))
            # 精确别名优先；模糊查询仅搜索卡名，百分号和下划线都是普通文字。
            row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                             'WHERE n.normalized=? ORDER BY c.konami_cid LIMIT 1', (key,)).fetchone()
            if row is None:
                row = db.execute('SELECT c.* FROM cards c JOIN card_names n ON n.card_id=c.id '
                                 'WHERE instr(n.normalized,?)>0 '
                                 'ORDER BY length(n.normalized),c.konami_cid LIMIT 1', (key,)).fetchone()
            return self._info(db, row)

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
            if target is None or not target['type_code'] & 1:
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
