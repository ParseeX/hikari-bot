"""简中版本补充：复用集换社卡片身份，不改写原日版快照或主库。"""
import asyncio
from collections import OrderedDict
import time

from .client import JhsUnavailable
from .models import CardVersion, normalized
from .version_catalog import VersionCatalog


class SimplifiedVersions:
    def __init__(self, client, *, ttl=3600, clock=time.monotonic):
        self.client, self.ttl, self.clock = client, ttl, clock
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()

    async def get(self, name, aliases, known):
        ids = sorted({v.card_id for v in known if v.card_id})
        key = (normalized(name), tuple(aliases), tuple(ids))
        async with self.lock:
            cached = self.cache.get(key)
            if cached and 0 <= self.clock() - cached[0] < self.ttl:
                self.cache.move_to_end(key)
                return list(cached[1])
            try:
                if not ids:
                    found = await self.client.versions(name, game_sub_key='sc')
                    names = {normalized(n) for n in aliases if n}
                    ids = sorted({v.card_id for v in found if v.card_id
                        and v.game_sub_key == 'sc' and (
                            normalized(v.name_jp) == normalized(name) if v.name_jp else
                            bool(names & {normalized(n) for n in (v.name_cn, *v.aliases)}))})
                versions = []
                for card_id in ids:
                    data = await self.client.complete_versions(card_id, game_sub_key='sc')
                    # 同一卡片可尚未发行简中；只有完整、身份匹配的空列表才算无版本。
                    if (data.get('complete') is not True or data.get('card_id') != card_id
                            or data.get('game_sub_key') != 'sc'
                            or normalized(data.get('name_jp') or '') != normalized(name)
                            or not isinstance(data.get('versions'), list)):
                        raise ValueError('简中卡片身份不匹配')
                    if data['versions']:
                        rows = VersionCatalog.validate([data], [card_id], name)
                        if any(row.get('game_sub_key') != 'sc' for row in rows):
                            raise ValueError('简中分区不匹配')
                        versions.extend(CardVersion.from_dict(row) for row in rows)
                if len({v.id for v in versions}) != len(versions):
                    raise ValueError('简中版本身份冲突')
            except (KeyError, TypeError, ValueError) as error:
                raise JhsUnavailable('简中版本列表暂时不可用') from error
            self.cache[key] = (self.clock(), tuple(versions))
            self.cache.move_to_end(key)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
            return versions
