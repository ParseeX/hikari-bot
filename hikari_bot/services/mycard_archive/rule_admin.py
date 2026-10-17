"""分类规则的原子编辑与并发覆盖保护。"""
import hashlib
import json
from pathlib import Path
from threading import RLock

from .presentation import validate_rules
from .store import atomic_write


_lock = RLock()


def representative_cards(data, catalog):
    """手选优先；默认按规则数组、规则内 cards 数组的原始顺序取首张有效卡。"""
    result = {}
    known = {}
    manual = data.get('representatives', {})
    for rule in data['rules']:
        label = rule['label'].strip()
        if label in result:
            continue
        if label in manual:
            result[label] = manual[label]
            continue
        for code in rule['cards']:
            if code not in known:
                known[code] = catalog.by_id(code) is not None
            if known[code]:
                result[label] = code
                break
    return result


class StaleRules(ValueError):
    pass


class RuleAdmin:
    def __init__(self, path: Path):
        self.path = path

    def read(self):
        raw = self.path.read_bytes() if self.path.exists() else b'{"version":1,"rules":[]}'
        data = json.loads(raw)
        validate_rules(data)
        representatives = data.get('representatives', {})
        if not isinstance(representatives, dict) or any(
            not isinstance(label, str) or type(code) is not int or not 0 < code < 2**27
            for label, code in representatives.items()
        ):
            raise ValueError('分类代表卡格式无效')
        return data, hashlib.sha256(raw).hexdigest()

    def representative(self, revision, label, card_id):
        with _lock:
            data, current = self.read()
            if revision != current:
                raise StaleRules('规则已更新，请刷新后重试')
            if label not in {rule['label'].strip() for rule in data['rules']}:
                raise ValueError('分类不存在')
            mapping = data.setdefault('representatives', {})
            if card_id is None:
                mapping.pop(label, None)
            elif type(card_id) is not int or not 0 < card_id < 2**27:
                raise ValueError('请选择有效卡片')
            else:
                mapping[label] = card_id
            self._write(data)

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            atomic_write(self.path.with_suffix('.json.bak'), self.path.read_bytes())
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

    def change(self, revision, *, index=None, rule=None):
        with _lock:
            data, current = self.read()
            if revision != current:
                raise StaleRules('规则已更新，请刷新后重试')
            rules = data['rules']
            if index is not None and not 0 <= index < len(rules):
                raise ValueError('规则不存在')
            if rule is not None:
                validate_rules({'version': 1, 'rules': [rule]})
                if any(existing == rule for i, existing in enumerate(rules) if i != index):
                    raise ValueError('这条映射已存在')
                if index is None:
                    rules.append(rule)
                else:
                    rules[index] = rule
            elif index is not None:
                rules.pop(index)
            else:
                raise ValueError('请选择要修改的映射')
            # 保留仅仍存在的分类配置，避免日后同名新分类意外继承旧图。
            labels = {item['label'].strip() for item in rules}
            if 'representatives' in data:
                data['representatives'] = {label: code for label, code in data['representatives'].items()
                                           if label in labels}
            self._write(data)
