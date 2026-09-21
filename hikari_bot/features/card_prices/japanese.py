"""按卖家备注查日版最低出品价；分页不完整时不声称找到了最低价。"""
import re
import unicodedata
from decimal import Decimal

from .models import CardVersion, money, normalized


def matches_japanese_note(note: str) -> bool:
    text = unicodedata.normalize('NFKC', note)
    return '日版' in text or re.search(r'(?<![^\W_])日(?![^\W_])', text) is not None


def positive_int(value) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError('invalid identifier')
    return int(value)


async def japanese_minimum(post, version: CardVersion) -> Decimal | None:
    async def pages(numbers):
        response = await post('/v1/listings', {'card_version_id': version.id, 'pages': numbers})
        result = response['pages']
        if len(result) != len(numbers) or {p['current_page'] for p in result} != set(numbers):
            raise ValueError('incomplete pages')
        return result

    first = (await pages([1]))[0]
    last = positive_int(first['last_page'])
    total = int(first['total'])
    if last > 100 or total < 0:
        raise ValueError('listing scan limit')
    collected = [first]
    for start in range(2, last + 1, 5):
        collected.extend(await pages(list(range(start, min(start + 5, last + 1)))))
    if any(int(p['last_page']) != last or int(p['total']) != total for p in collected):
        raise ValueError('listings changed during pagination')
    if sum(len(p['entries']) for p in collected) != total:
        raise ValueError('incomplete seller list')

    sellers: dict[int, Decimal] = {}
    for page in collected:
        for row in [*page['entries'], *page.get('pinned_entries', [])]:
            if int(row['card_version_id']) != version.id:
                raise ValueError('listing version mismatch')
            if int(row['quantity']) <= 0:
                continue
            seller = positive_int(row['seller_user_id'])
            lower = money(row['min_price'])
            if lower is None:
                raise ValueError('missing seller minimum')
            sellers[seller] = min(lower, sellers.get(seller, lower))

    best = None
    candidates = sorted(sellers, key=sellers.get)
    for offset in range(0, len(candidates), 5):
        # 卖家起价已不低于当前日版价，其任何在售出品都不能降低结果。
        batch = [s for s in candidates[offset:offset + 5] if best is None or sellers[s] < best]
        if not batch:
            break
        response = await post('/v1/listing-details', {'card_version_id': version.id, 'seller_ids': batch})
        details = response['sellers']
        if len(details) != len(batch) or {d['seller_id'] for d in details} != set(batch):
            raise ValueError('incomplete seller details')
        for detail in details:
            if (normalized(detail['number']) != normalized(version.number)
                    or str(detail['rarity']).upper() != version.rarity
                    or (version.card_id and int(detail['card_id']) != version.card_id)):
                raise ValueError('seller card mismatch')
            products = list(detail['products'])
            if detail.get('default_product'):
                products.append(detail['default_product'])
            for product in products:
                if product.get('pull_off') in (True, 1, '1') or int(product['quantity']) <= 0:
                    continue
                note = product.get('remark_only')
                if note is None:
                    note = product.get('remark') or ''
                if not matches_japanese_note(str(note)):
                    continue
                price = money(product['price'])
                if price is None:
                    raise ValueError('missing listing price')
                best = price if best is None else min(best, price)
    return best
