"""用相同模板渲染完整示例和子段落；不查询业务数据，也不发送消息。"""
from .output_templates import SCHEMA, OVERRIDE, present, validate


def render_example(store, key, spec, example=None):
    definition = SCHEMA['states'][key]
    root = definition.get('preview_root', key)
    root_def = SCHEMA['states'][root]
    state = store.state()
    overrides = {name: item['draft'] for name, item in state['states'].items()}
    overrides[key] = validate(key, spec)
    examples = root_def.get('examples', [{'id': 'default', 'label': '实际回复示例（数据为虚构）'}])
    selected = next((e for e in examples if e['id'] == example), None)
    def includes(name, target, seen=None):
        seen = set(seen or ())
        if name in seen:
            return False
        seen.add(name)
        return any(item['key'] == target or includes(item['key'], target, seen)
                   for part in SCHEMA['states'][name].get('parts', {}).values() for item in part['items'])

    if selected is None and key != root and not includes(root, key):
        selected = next((e for e in examples if any(item.get('key') == key
            for group in [e.get('parts', {}), *e.get('nested_parts', {}).values()]
            for items in group.values() for item in items)), None)
    selected = selected or examples[0]
    values_by_key = {}
    rendered_by_key = {}

    def render(name, extra=None, depth=0):
        if depth > 8:
            raise ValueError('示例段落嵌套过深')
        d = SCHEMA['states'][name]
        values = {**d['samples'], **(extra or {})}
        if name == root:
            values.update(selected.get('values', {}))
        for field, part in d.get('parts', {}).items():
            choices = selected.get('parts', {}) if name == root else selected.get('nested_parts', {}).get(name, {})
            items = choices.get(field, part['items'])
            values[field] = part.get('separator', '').join(str(render(item['key'], item.get('values'), depth+1)) for item in items)
        # 兼容用户已有的前后缀 + {content}，但新页面不再只显示整个黑盒。
        if d.get('legacy_template') in {'{content}', '{body}'}:
            old_field = d['legacy_template'][1:-1]
            values[old_field] = d['template'].format_map({n: str(values.get(n, '')) for n in d['fields']})
        result = present(name, values)
        values_by_key[name], rendered_by_key[name] = values, result
        return result

    token = OVERRIDE.set(overrides)
    try:
        context = render(root)
        current = rendered_by_key.get(key)
        unrepresented = current is None
        if current is None:
            current = render(key)
        values = values_by_key[key]
        return {'text': str(current), 'mode': overrides[root]['mode'] if definition.get('fragment') else current.mode,
                'context': str(context), 'context_mode': context.mode, 'root_key': root,
                'root_label': root_def['label'], 'example': selected['id'], 'examples': examples,
                'values': {name: str(values.get(name, '')) for name in definition['fields']},
                'media_note': root_def.get('media_note', ''),
                'unrepresented': unrepresented}
    finally:
        OVERRIDE.reset(token)
