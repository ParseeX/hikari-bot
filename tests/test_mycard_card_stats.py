from types import SimpleNamespace

from hikari_bot.services.mycard_archive.card_stats import card_usage
from hikari_bot.services.mycard_archive.replay import Deck


def version(main=(), side=(), extra=()):
    return SimpleNamespace(deck=Deck('ignored',main,extra,side))


def test_users_union_averages_side_extra_and_top100_scope():
    latest={'A':version((1,1,1),(),(9,)), 'B':version((),(1,1,2)),
            'C':version((2,), (2,)), 'outside':version((8,))}
    catalog=SimpleNamespace(cards_by_id=lambda codes:{code:{'id':code,'cn_name':f'卡{code}'} for code in codes})
    result=card_usage(['A','B','C','missing'],latest,catalog)
    assert result['recorded_players']==3 and result['total']==4
    assert [card['code'] for card in result['cards']]==[1,2]
    one,two=result['cards']
    assert (one['users'],one['main_average'],one['side_average'])==(2,1.5,1)
    assert (two['users'],two['main_average'],two['side_average'])==(2,.5,1)
    assert one['main_users']==[0,0,1] and one['side_users']==[0,1,0]
    assert two['main_users']==[1,0,0] and two['side_users']==[2,0,0]


def test_explicit_identity_merges_variants_but_not_unrelated_names():
    calls=[]
    def info(codes):
        calls.append(codes)
        return {1:{'id':1,'cn_name':'同名'},11:{'id':1,'cn_name':'同名'},2:{'id':2,'cn_name':'同名'},3:None}
    latest={'A':version((1,11,2),(11,3)), 'B':version((11,))}
    cards=card_usage(['A','B'],latest,SimpleNamespace(cards_by_id=info))['cards']
    assert len(calls)==1 and calls[0]=={1,11,2,3}
    assert [card['code'] for card in cards]==[1,2,3]
    assert (cards[0]['users'],cards[0]['main_average'],cards[0]['side_average'])==(2,1.5,.5)
    assert cards[0]['main_users']==[1,1,0] and cards[0]['side_users']==[1,0,0]
    assert cards[2]['name']=='卡片 3'


def test_no_recorded_decks_produce_empty_table():
    catalog=SimpleNamespace(cards_by_id=lambda codes:{})
    assert card_usage(['missing'],{},catalog)=={'cards':[],'total':1,'recorded_players':0}


def test_each_zone_counts_people_at_exact_copy_count():
    latest={str(i):version((1,)*i,(1,)*(4-i)) for i in range(1,4)}
    latest['both']=version((1,1),(1,))
    latest['zero']=version((),(),(1,1,1))
    catalog=SimpleNamespace(cards_by_id=lambda codes:{})
    card=card_usage(list(latest),latest,catalog)['cards'][0]
    assert card['users']==4
    assert card['main_users']==[1,2,1]
    assert card['side_users']==[2,1,1]


def test_counts_above_three_are_not_misreported_as_three():
    catalog=SimpleNamespace(cards_by_id=lambda codes:{})
    card=card_usage(['A'],{'A':version((1,)*4,(1,)*4)},catalog)['cards'][0]
    assert card['users']==1
    assert card['main_users']==card['side_users']==[0,0,0]
