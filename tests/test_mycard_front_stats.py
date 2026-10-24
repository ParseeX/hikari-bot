import asyncio
import pytest
from hikari_bot.services.mycard_archive.front_stats import FrontStats, official_stats


@pytest.mark.parametrize('data', [None, [], {}, {'arena_rank': 0, 'pt': 1000},
    {'arena_rank': True}, {'arena_rank': -1}, {'arena_rank': '1.2'}])
def test_missing_rank_never_becomes_zero_or_default_score(data):
    assert official_stats(data) == {'rank': None, 'score': None,
                                    'month_games': None, 'month_win_rate': None}


def test_mapping_does_not_mislabel_unscoped_or_previous_month_counts():
    data = {'arena_rank': '42', 'pt': 1234, 'exp': 8888, 'exp_rank': 999,
            'athletic_all': 123, 'athletic_wl_ratio': '70.00',
            'season': '2026-09', 'athleticWin': 100, 'athleticLose': 23}
    assert official_stats(data) == {'rank': 42, 'score': 1234,
                                    'month_games': None, 'month_win_rate': None}
    assert official_stats({'arena_rank': 1, 'pt': 0})['score'] == 0
    for bad in (None, True, 'NaN', float('nan'), float('inf')):
        assert official_stats({'arena_rank': 1, 'pt': bad})['score'] is None
