import asyncio
from decimal import Decimal

import pytest

from hikari_bot.features.card_prices.client import JhsClient
from hikari_bot.features.card_prices.japanese import japanese_minimum, matches_japanese_note
from hikari_bot.features.card_prices.models import CardVersion


@pytest.mark.parametrize('note,expected', [
    ('日', True), ('【日】轻微划痕', True), ('日/近全新', True), ('日 轻痕', True),
    ('全新日版', True), ('日版', True), ('今日发货', False), ('日文', False),
    ('日卡', False), ('港版', False), ('', False), ('日日', False),
])
def test_user_requested_note_rule(note, expected):
    assert matches_japanese_note(note) == expected


VERSION = CardVersion(504446, 'LOCR-JP076', 'UTR', card_id=16724)


class Listings:
    def __init__(self):
        self.calls = []
        self.incomplete = False
        self.mismatch = False

    async def post(self, path, body):
        self.calls.append((path, body))
        if path == '/v1/listings':
            rows = [[1, 2, 3], [4, 5], [6, 7]]
            pages = [{'current_page': p, 'last_page': 3, 'total': 7, 'pinned_entries': [],
                      'entries': [{'seller_user_id': s, 'card_version_id': VERSION.id,
                                   'quantity': 1, 'min_price': '100' if s == 7 else '1'}
                                  for s in rows[p - 1]]} for p in body['pages']]
            return {'pages': pages[:-1] if self.incomplete and body['pages'] != [1] else pages}
        assert path == '/v1/listing-details'  # 日版查询不读取价格历史或通用最低价。
        notes = {1: ('日版', 9), 2: ('港版', 1), 3: ('【日】', 7), 4: ('今日发货', 1),
                 5: ('', 1), 6: ('日', 5), 7: ('日版', 100)}
        return {'sellers': [{
            'seller_id': seller, 'number': 'OTHER-JP001' if self.mismatch else VERSION.number,
            'rarity': 'UTR', 'card_id': 16724,
            'products': [{'price': notes[seller][1], 'quantity': 1, 'remark_only': notes[seller][0]}],
            'default_product': {'price': '.01', 'quantity': 0, 'pull_off': True, 'remark_only': '日版'},
        } for seller in body['seller_ids']]}


def test_minimum_in_later_page_and_sold_out_default_excluded():
    source = Listings()
    assert asyncio.run(japanese_minimum(source.post, VERSION)) == Decimal('5')
    assert [body['pages'] for path, body in source.calls if path == '/v1/listings'] == [[1], [2, 3]]
    assert [body['seller_ids'] for path, body in source.calls if path == '/v1/listing-details'] == [[1, 2, 3, 4, 5], [6]]


@pytest.mark.parametrize('failure', ['incomplete', 'mismatch'])
def test_incomplete_or_wrong_card_never_becomes_a_false_minimum(failure):
    source = Listings()
    setattr(source, failure, True)
    client = JhsClient('http://localhost', 'test')
    client._post = source.post
    result = asyncio.run(client.japanese_prices([VERSION]))[VERSION.id]
    assert result.error and result.minimum is None


def test_empty_market_is_not_an_error():
    async def post(path, body):
        assert path == '/v1/listings'
        return {'pages': [{'current_page': 1, 'last_page': 1, 'total': 0, 'entries': []}]}
    assert asyncio.run(japanese_minimum(post, VERSION)) is None
