"""从日文官方收录目录生成按日期倒序的集换社卡盒采集清单。"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
from urllib.parse import parse_qs, urlparse
import urllib.request

from bs4 import BeautifulSoup

if __package__:
    from .crawl_jhs import save_state
else:
    from crawl_jhs import save_state


BASE = 'https://www.db.yugioh-card.com/yugiohdb/'
DIRECTORY = BASE + 'card_list.action?request_locale=ja'
CARD_CATEGORIES = {'基本ブースターパック', '構築済みデッキ', 'その他ブースターパック',
                   'デュエルターミナル', '商品同梱'}


class OfficialUnavailable(RuntimeError):
    """官网暂不可用时停止整轮，避免把不完整目录当作成功。"""


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def parameter(value, name):
    values = parse_qs(urlparse(value).query).get(name, [])
    return values[0] if values and re.fullmatch(r'\d+', values[0]) else None


def product_url(pid):
    return BASE + f'card_search.action?ope=1&sess=1&pid={pid}&rp=99999&request_locale=ja'


def parse_directory(html):
    soup = BeautifulSoup(html, 'html.parser')
    products = {}
    for row in soup.select('.t_row'):
        link = row.select_one('input.link_value[value*="pid="]')
        if not link:
            continue
        pid = parameter(link['value'], 'pid')
        raw_date = text(row.select_one('.time'))
        name = text(row.select_one('.main p'))
        category = text(row.select_one('.catergory .ws_nowrap')).strip('【】')
        if not pid or not re.fullmatch(r'\d{4}/\d{2}/\d{2}', raw_date) or not name:
            raise ValueError('官方商品目录结构变化或日期不完整')
        products[pid] = {'pid': pid, 'name': name, 'date': date.fromisoformat(raw_date.replace('/', '-')).isoformat(),
                         'category': category, 'source_url': product_url(pid)}
    if not products:
        raise ValueError('官方目录为空，拒绝生成空清单')
    return sorted(products.values(), key=lambda p: p['date'], reverse=True)


def parse_product_cards(html):
    soup = BeautifulSoup(html, 'html.parser')
    ids = []
    for link in soup.select('#card_list > .t_row input.link_value'):
        cid = parameter(link.get('value', ''), 'cid')
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        raise ValueError('官方商品没有返回卡片列表')
    return ids


def parse_printings(html):
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select_one('#update_list'):
        raise ValueError('官方卡片页面缺少收录区')
    result = {}
    for row in soup.select('#update_list .t_row'):
        link = row.select_one('input.link_value')
        if not link:
            continue
        pid = parameter(link.get('value', ''), 'pid')
        number = unicodedata.normalize('NFKC', text(row.select_one('.card_number'))).strip()
        match = re.fullmatch(r'([A-Z0-9]{1,16})-[A-Z0-9]+', number)
        if pid and match:
            result.setdefault(pid, set()).add(match[1])
    return result


class OfficialPages:
    def __init__(self, cache: Path, interval=1.0):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.interval = interval
        self.last_request = 0.0
        self.requests = 0

    def get(self, key, url, *, fresh=False):
        path = self.cache / (key + '.html')
        if path.exists() and not fresh:
            return path.read_text(encoding='utf-8')
        for attempt in range(3):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'HikariCardCatalog/1.0'})
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = response.read(8 * 1024 * 1024)
                self.requests += 1
                html = raw.decode('utf-8')
                temporary = path.with_suffix('.html.tmp')
                temporary.write_text(html, encoding='utf-8')
                temporary.replace(path)
                return html
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise OfficialUnavailable(f'官网请求失败 HTTP {code}') from None
                time.sleep(60 if code == 429 else 5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise OfficialUnavailable('官网连接失败') from None
                time.sleep(5 * (attempt + 1))


def make_manifest(products, resolved):
    packs = {}
    for product in sorted(products, key=lambda p: p['date'], reverse=True):
        for prefix in sorted(resolved.get(product['pid'], [])):
            if prefix not in packs:
                packs[prefix] = {'prefix': prefix, 'name': product['name'],
                                 'release_date': product['date'], 'source_url': product['source_url']}
    return list(packs.values())


def discover(pages, output, *, as_of, include_promos=False, limit=None, emit=print):
    products = parse_directory(pages.get('directory', DIRECTORY, fresh=True))
    selected = [p for p in products if p['date'] <= as_of and
                (include_promos or p['category'] in CARD_CATEGORIES)]
    if limit:
        selected = selected[:limit]
    if not selected:
        raise ValueError('指定范围内没有官方商品，保留现有清单')
    # 缓存卡片详情会同时发现多个收录商品的编号，减少重复访问。
    known = {}
    for path in sorted(pages.cache.glob('card-*.html')):
        for pid, prefixes in parse_printings(path.read_text(encoding='utf-8')).items():
            known.setdefault(pid, set()).update(prefixes)
    resolved, unresolved = {}, []
    report_path = output.with_suffix('.report.json')
    for index, product in enumerate(selected, 1):
        pid = product['pid']
        try:
            if pid not in known:
                cids = parse_product_cards(pages.get('product-' + pid, product['source_url']))
                # 核对首、中、末三张卡的收录；不同子包可能使用不同前缀。
                probes = list(dict.fromkeys([cids[0], cids[len(cids) // 2], cids[-1]]))
                for cid in probes:
                    url = BASE + f'card_search.action?ope=2&cid={cid}&request_locale=ja'
                    for found_pid, prefixes in parse_printings(pages.get('card-' + cid, url)).items():
                        known.setdefault(found_pid, set()).update(prefixes)
            if pid in known:
                resolved[pid] = sorted(known[pid])
            else:
                unresolved.append({**product, 'reason': '抽查卡片没有可确认的编号前缀'})
        except ValueError as error:
            unresolved.append({**product, 'reason': str(error)})
        # 后续卡片可能补充较早已见商品的其他子包编号。
        resolved = {p['pid']: sorted(known[p['pid']]) for p in selected[:index] if p['pid'] in known}
        manifest = make_manifest(selected[:index], resolved)
        save_state(output, manifest)
        report = {'source': DIRECTORY, 'as_of': as_of, 'include_promos': include_promos,
                  'directory_products': len(products), 'selected_products': len(selected),
                  'processed_products': index, 'prefixes': len(manifest),
                  'resolved': {key: value for key, value in resolved.items()},
                  'unresolved': unresolved, 'products': selected[:index]}
        save_state(report_path, report)
        emit(f'[{index}/{len(selected)}] {product["date"]} pid={pid} '
             f'prefix={",".join(resolved.get(pid, [])) or "待核对"} 累计 {len(manifest)} 个盒号', flush=True)
    return make_manifest(selected, resolved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--as-of', default=date.today().isoformat())
    parser.add_argument('--include-promos', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    date.fromisoformat(args.as_of)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit 必须为正整数')
    pages = OfficialPages(args.cache_dir.expanduser())
    rows = discover(pages, args.output.expanduser(), as_of=args.as_of,
                    include_promos=args.include_promos, limit=args.limit)
    print(json.dumps({'prefixes': len(rows), 'http_requests': pages.requests}, ensure_ascii=False))


if __name__ == '__main__':
    main()
