from hikari_bot.services.card_catalog import CardCatalog
from scripts.card_catalog import catalog as cat
from test_card_catalog import card
from hikari_bot.services.mycard_archive.presentation import classify
from hikari_bot.services.mycard_archive.replay import Deck


def test_search_many_returns_all_cards_deduplicates_names_and_prioritizes_exact(tmp_path):
    path = tmp_path / 'cards.sqlite3'
    db = cat.connect(path)
    records = [card(cid=i, passcode=1000 + i, name=f'系列卡片{i}') for i in range(1, 26)]
    records.append(card(cid=99, passcode=1099, name='系列'))
    with db:
        cat.import_cards(db, {str(c['cid']): c for c in records}, {})
    db.close()
    catalog = CardCatalog(path)
    results = catalog.search_many(' 系列 ')
    assert len(results) == 26
    assert results[0]['id'] == 1099
    assert len({row['id'] for row in results}) == 26
    assert len(catalog.search_many('青眼の白龍')) == 26
    assert [row['id'] for row in catalog.search_many('００００１００１')] == [1001]
    assert catalog.search_many('不存在') == []
    assert catalog.search_many('') == []
    assert catalog.search_many('%') == []
    assert catalog.search_many('_') == []


def test_classification_matches_artwork_both_directions_but_not_other_card_or_side(tmp_path):
    path = tmp_path / 'cards.sqlite3'
    db = cat.connect(path)
    with db:
        cat.import_cards(db, {'1':card(cid=1, passcode=123, name='同名测试'),
                             '2':card(cid=2, passcode=456, name='同名测试')}, {})
        card_id = db.execute("SELECT id FROM cards WHERE passcode='00000123'").fetchone()[0]
        db.execute('INSERT INTO card_artwork_ids VALUES(?,?,?)', (124, card_id, 'test'))
    db.close()
    catalog = CardCatalog(path)
    rules = [{'label':'甲','cards':[123]}, {'label':'乙','cards':[124]},
             {'label':'组合','cards':[123,456],'match':'all'}]
    deck = Deck('玩家', (124,), (456,), (456,))
    result = classify(deck, rules, catalog=catalog)
    assert result['labels'] == ['甲', '乙']
    assert result['evidence'][0]['cards'] == [124]
    assert deck.main == (124,)
    assert classify(Deck('玩家', (123,456), ()), rules, catalog=catalog)['labels'] == ['甲','乙','组合']
    assert classify(Deck('玩家', (456,), (123,), (124,)), rules, catalog=catalog)['labels'] == []
    assert catalog.canonical_codes([123,124,456,999]) == {123:123,124:123,456:456,999:999}
    assert classify(Deck('玩家',(999,),()), [{'label':'未知卡','cards':[999]}], catalog=catalog)['labels'] == ['未知卡']
