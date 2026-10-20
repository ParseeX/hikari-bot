"""手动卡组独立保存，不生成或改写对局记录。"""
import base64
import hashlib
import json
import re
from pathlib import Path
from threading import RLock
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .replay import Deck
from .store import atomic_write

_lock = RLock()


class StaleManualDecks(ValueError):
    pass


def parse_link(player, link):
    """只解码链接内的 OURYGO v0/v1 卡组数据，不访问链接目标。"""
    if not isinstance(link, str) or len(link) > 8192:
        raise ValueError('卡组链接过长')
    try:
        url = urlsplit(link.strip())
        query = parse_qs(url.query, strict_parsing=True, keep_blank_values=True)
        if (url.scheme not in ('http', 'https', 'ygo') or not url.netloc
                or query.get('ygotype') != ['deck']):
            raise ValueError()
        version = query.get('v', ['1'])
        if version == ['0']:
            sections = _parse_v0(query)
        elif version == ['1']:
            sections = _parse_v1(query)
        else:
            raise ValueError()
        if not 40 <= len(sections[0]) <= 60 or any(len(s) > 15 for s in sections[1:]):
            raise ValueError()
        return Deck(player, *sections)
    except (ValueError, TypeError):
        raise ValueError('卡组链接无效，请使用 OURYGO v=0 或 v=1 分享链接（主卡组 40–60 张，额外和副卡组各不超过 15 张）') from None


def _parse_v0(query):
    sections = []
    for name, limit in (('main', 60), ('extra', 15), ('side', 15)):
        values = query.get(name, [] if name == 'main' else [''])
        if len(values) != 1:
            raise ValueError()
        cards = []
        for item in values[0].split('_') if values[0] else []:
            match = re.fullmatch(r'([0-9]{1,9})(?:\*([0-9]{1,2}))?', item)
            if not match:
                raise ValueError()
            code, quantity = int(match[1]), int(match[2] or 1)
            # 展开前限制数量，避免畸形链接造成超大分配。
            if not 0 < code < 2**27 or not 1 <= quantity <= limit - len(cards):
                raise ValueError()
            cards.extend([code] * quantity)
        sections.append(tuple(sorted(cards)))
    return sections


def _parse_v1(query):
    if len(query.get('d', [])) != 1:
        raise ValueError()
    encoded = query['d'][0]
    payload = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
    if len(payload) < 2:
        raise ValueError()
    bits = ''.join(f'{byte:08b}' for byte in payload)
    counts = [int(bits[:8], 2), int(bits[8:12], 2), int(bits[12:16], 2)]
    needed = 16 + sum(counts) * 29
    if len(bits) != (needed + 7) // 8 * 8 or '1' in bits[needed:]:
        raise ValueError()
    sections, offset = [], 16
    for count in counts:
        cards = []
        for _ in range(count):
            quantity = int(bits[offset:offset + 2], 2)
            code = int(bits[offset + 2:offset + 29], 2)
            if not quantity or not code:
                raise ValueError()
            cards.extend([code] * quantity)
            offset += 29
        sections.append(tuple(sorted(cards)))
    return sections


class ManualDecks:
    def __init__(self, path: Path):
        self.path = path

    def read(self):
        raw = self.path.read_bytes() if self.path.exists() else b'{"version":1,"decks":[]}'
        data = json.loads(raw)
        if data.get('version') != 1 or not isinstance(data.get('decks'), list):
            raise ValueError('手动卡组文件格式无效')
        return data['decks'], hashlib.sha256(raw).hexdigest()

    def save(self, revision, *, entry_id, player, link, catalog):
        player = player.strip()
        if not player or len(player) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in player):
            raise ValueError('请输入有效的 MyCard 用户名')
        with _lock:
            entries, current = self.read()
            if current != revision:
                raise StaleManualDecks('卡组已更新，请刷新列表后重试')
            previous = next((entry for entry in entries if entry['id'] == entry_id), None)
            if entry_id is not None and previous is None:
                raise ValueError('要修改的卡组不存在')
            if any(entry['player'] == player and entry['id'] != entry_id for entry in entries):
                raise ValueError('该用户名已有手动卡组，请编辑已有记录')
            if link is None:
                if previous is None:
                    raise ValueError('请填写卡组链接')
                updated = {**previous, 'player': player}
            else:
                deck = parse_link(player, link)
                for code in set(deck.main + deck.extra + deck.side):
                    if catalog.by_id(code) is None:
                        raise ValueError(f'卡片资料中未找到卡号 {code}，请检查卡组链接')
                updated = {'id': entry_id or uuid4().hex, 'player': player,
                           'main': list(deck.main), 'extra': list(deck.extra), 'side': list(deck.side),
                           'updated_at': time.time()}
            if previous is None:
                entries.append(updated)
            else:
                entries[entries.index(previous)] = updated
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                atomic_write(self.path.with_suffix('.json.bak'), self.path.read_bytes())
            atomic_write(self.path, json.dumps({'version': 1, 'decks': entries},
                                              ensure_ascii=False).encode('utf-8'))
            return updated
