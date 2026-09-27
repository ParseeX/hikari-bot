"""在真实 Worker 方法上验证分页、共有版本和混合周边结果。"""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def worker(monkeypatch):
    for name in ['frida', 'frida_tools', 'phone']:
        module = ModuleType(name)
        if name == 'phone':
            module.BASE, module.Phone = Path('.'), object
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('worker_catalog_test', 'scripts/jihuanshe_bridge/worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Worker.__new__(module.Worker)


def test_large_pack_reads_beyond_page_30(worker):
    def query(template, payload):
        page = payload['page']
        return {'entries': [{'id': page}], 'last_page': 45, 'current_page': page}
    worker.query = query
    assert len(worker.versions('QCCP')['versions']) == 45


def test_card_detail_validates_identity_and_limits_fields(worker):
    worker.query = lambda *_: {'card': {'id': 71, 'name_cn': '迅捷飞鼠',
        'name_jp': '素早いモモンガ', 'type': '效果怪兽', 'extra': 'discard'}}
    assert set(worker.card_detail(71)['card']) == {'id', 'name_cn', 'name_jp', 'type'}
    with pytest.raises(ValueError, match='invalid_card_identity'):
        worker.card_detail(3298)


def test_shared_product_verifies_series_sample_not_single_detail_parent(worker):
    product = {'id': 4636, 'version_count': 2, 'sample_version_ids': [1, 2]}
    def query(template, payload):
        assert template == 'product-search.js'
        return {'product': product, 'entries': [{'id': 1, 'object_type': 'card'}, {'id': 2, 'object_type': 'card'},
                                                {'id': 3, 'object_type': 'goods'}],
                'total': 3, 'current_page': 1, 'last_page': 1}
    worker.query = query
    result = worker.product_versions(4636)
    assert len(result['versions']) == 3  # 保留分类，让入库端隔离旧误录周边。


def test_product_filter_still_rejects_wrong_series(worker):
    worker.query = lambda *_: {'product': {'id': 1, 'version_count': 1, 'sample_version_ids': [99]},
                              'entries': [{'id': 2, 'object_type': 'card'}],
                              'current_page': 1, 'last_page': 1, 'total': 1}
    with pytest.raises(ValueError):
        worker.product_versions(1)


@pytest.mark.parametrize('reported', [38, 40, 0, None, '39'])
def test_reference_count_does_not_reject_complete_card_list(worker, reported):
    # SD26 实际为 39 个版本；不同的详情计数都不应改变完整列表的验收结果。
    def query(template, payload):
        page = payload['page']
        rows = [{'id': i, 'object_type': 'card'} for i in range(1, 40)]
        return {'product': {'id': 588, 'version_count': reported, 'sample_version_ids': [1, 2, 3]} if page == 1 else None,
                'entries': rows[(page-1)*15:page*15], 'current_page': page, 'last_page': 3, 'total': 40}
    worker.query = query
    result = worker.product_versions(588)
    assert len(result['versions']) == 39
    assert result['card_count'] == 39
    assert result['count_mismatch'] is True


def test_zero_reference_count_cannot_bypass_identity_check(worker):
    worker.query = lambda *_: {'product': {'id': 1, 'version_count': 0, 'sample_version_ids': [99]},
                              'entries': [{'id': 2, 'object_type': 'card'}],
                              'current_page': 1, 'last_page': 1, 'total': 1}
    with pytest.raises(ValueError, match='product_filter_ignored'):
        worker.product_versions(1)


def test_empty_product_is_still_rejected(worker):
    worker.query = lambda *_: {'product': {'id': 1, 'version_count': 0, 'sample_version_ids': [1]},
                              'entries': [], 'current_page': 1, 'last_page': 1, 'total': 0}
    with pytest.raises(ValueError):
        worker.product_versions(1)
