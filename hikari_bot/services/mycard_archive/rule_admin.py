"""分类规则的原子编辑与并发覆盖保护。"""
import hashlib
import json
from pathlib import Path
from threading import RLock

from .presentation import validate_rules
from .store import atomic_write


_lock = RLock()


class StaleRules(ValueError):
    pass


class RuleAdmin:
    def __init__(self, path: Path):
        self.path = path

    def read(self):
        raw = self.path.read_bytes() if self.path.exists() else b'{"version":1,"rules":[]}'
        data = json.loads(raw)
        validate_rules(data)
        return data, hashlib.sha256(raw).hexdigest()

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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 保存前一版，写入失败时原规则仍可读；不重写历史归档。
            if self.path.exists():
                atomic_write(self.path.with_suffix('.json.bak'), self.path.read_bytes())
            atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
