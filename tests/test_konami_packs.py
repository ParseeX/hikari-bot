"""官方目录的排序、前缀证据及缓存发现流程验证。"""
import json

import pytest

from scripts.card_catalog import konami_packs as source


def product(pid, stamp, name, category='基本ブースターパック'):
    return f'<div class="t_row"><div class="time">{stamp}</div><div class="catergory"><span class="ws_nowrap">【{category}】</span></div><div class="main"><p>{name}</p><input class="link_value" value="card_search.action?pid={pid}"/></div></div>'


def test_directory_dates_and_categories():
    html = product(1, '2024/07/27', '旧盒') + product(2, '2026/09/05', '新盒')
    rows = source.parse_directory(html)
    assert [p['pid'] for p in rows] == ['2', '1']
    assert rows[0]['date'] == '2026-09-05'
    assert rows[0]['category'] == '基本ブースターパック'
    with pytest.raises(ValueError):
        source.parse_directory('<html>blocked</html>')


def test_prefix_requires_official_printing_number_and_matching_pid():
    html = '''<div id="update_list">
      <div class="t_row"><span class="card_number">DBGV-JP001</span><input class="link_value" value="x?pid=1"/></div>
      <div class="t_row"><span class="card_number">DBGV-JP001</span><input class="link_value" value="x?pid=1"/></div>
      <div class="t_row"><span class="card_number">15AY-JPA01</span><input class="link_value" value="x?pid=2"/></div>
      <div class="t_row"><span class="card_number"></span><input class="link_value" value="x?pid=3"/></div>
    </div>'''
    assert source.parse_printings(html) == {'1': {'DBGV'}, '2': {'15AY'}}


def test_manifest_deduplicates_prefix_using_newest_official_date():
    rows = source.parse_directory(product(1, '2024/01/01', '旧') + product(2, '2026/01/01', '新'))
    assert source.make_manifest(rows, {'1': ['ABC'], '2': ['ABC', 'XYZ']}) == [
        {'prefix': code, 'name': '新', 'release_date': '2026-01-01', 'source_url': source.product_url('2')}
        for code in ['ABC', 'XYZ']]


def test_discovery_excludes_future_and_promos_and_keeps_unresolved(tmp_path):
    class Pages:
        cache = tmp_path
        def get(self, key, url, **kwargs):
            if key == 'directory':
                return (product(1, '2026/09/05', '新盒') + product(2, '2026/07/01', '旧盒') +
                        product(3, '2026/09/26', '未发售') + product(4, '2026/09/18', '杂志', '書籍'))
            if key.startswith('product-'):
                cid = key.split('-')[1]
                return f'<div id="card_list"><div class="t_row"><input class="link_value" value="x?cid={cid}"/></div></div>'
            if key == 'card-1':
                return '<div id="update_list"><div class="t_row"><span class="card_number">DBGV-JP001</span><input class="link_value" value="x?pid=1"/></div></div>'
            return '<div id="update_list"></div>'
    output = tmp_path / 'packs.json'
    result = source.discover(Pages(), output, as_of='2026-09-24', emit=lambda *args, **kwargs: None)
    assert [p['prefix'] for p in result] == ['DBGV']
    report = json.loads(output.with_suffix('.report.json').read_text(encoding='utf-8'))
    assert report['selected_products'] == report['processed_products'] == 2
    assert [p['pid'] for p in report['unresolved']] == ['2']
    assert json.loads(output.read_text(encoding='utf-8')) == result
