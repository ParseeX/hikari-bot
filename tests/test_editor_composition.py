"""验证编辑的是实际回复段落，而非与业务脱节的示意文字。"""
import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from hikari_bot.qq_official import output_templates as templates
from hikari_bot.qq_official.output_templates import SCHEMA, Store, default, present
from hikari_bot.qq_official.editor_examples import render_example
from hikari_bot.services.wording import using_wording

HISTORY = 'history.mycard_reports_body_2fe0148e'
SUMMARY = 'history.mycard_reports_e55ebb30'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'HOME', tmp_path)
    return Store(tmp_path)


def publish(store, key, **changes):
    spec = store.state()['states'][key]['draft']
    spec.update(changes)
    revision = store.load()['revision']
    feature = SCHEMA['states'][key]['feature']
    store.mutate('save', feature, key, spec, revision)
    store.mutate('publish', feature, revision=revision+1)


def test_every_scene_preview_is_resolvable_and_not_an_opaque_placeholder(store):
    for key, definition in SCHEMA['states'].items():
        if definition['feature'] == 'tournament':
            continue
        assert definition['category'] in {'main', 'part', 'error'}, key
        result = render_example(store, key, default(key))
        assert result['root_key'] in SCHEMA['states']
        assert not any(text in result['context'] for text in ['示例查询内容', '下一段示例内容']), key
        for part in definition.get('parts', {}).values():
            assert all(item['key'] in SCHEMA['states'] for item in part['items'])
        for example in definition.get('examples', []):
            assert render_example(store, key, default(key), example['id'])['example'] == example['id']


def test_history_scenarios_match_optional_score_and_rank(store):
    current = render_example(store, HISTORY, default(HISTORY), 'current')['context']
    assert '11月场次：128' in current and '胜率：62.50%' in current
    assert '当前分数：1523.45' in current and '当前排名：86' in current
    assert '311月' not in current
    settled = render_example(store, HISTORY, default(HISTORY), 'settled')['context']
    assert '结算分数：1488.20' in settled and '结算排名：102' in settled
    empty = render_example(store, HISTORY, default(HISTORY), 'empty')['context']
    assert '场次：0' in empty and '胜率：0.00%' in empty and '分数' not in empty and '排名' not in empty
    assert '排名' not in render_example(store, HISTORY, default(HISTORY), 'no_rank')['context']


def test_editing_child_updates_full_preview_and_reordering_root_uses_drafts(store):
    root = default(HISTORY)
    root['template'] = '{ranking}\n{summary}{score}'
    store.mutate('save', 'history', HISTORY, root, 0)
    child = default(SUMMARY)
    child['template'] = '胜率 {value7}%｜场次 {value4}\n选手 {user_id}'
    result = render_example(store, SUMMARY, child)
    assert result['context'].lstrip().startswith('当前排名：86')
    assert '胜率 62.50%｜场次 128\n选手 示例玩家A' in result['context']
    assert not store.queue.exists()
    assert store.active(HISTORY)['template'] != root['template']


def test_reading_old_default_wrapper_preserves_custom_errors_and_file_bytes(store):
    old = default(HISTORY)
    old.update(template='{content}', visible=['content'])
    error_key = 'history.mycard_reports_b98c4ab7'
    error = default(error_key); error['template'] = '暂时查不到，等会儿再试。'
    store.atomic(store.path, {'schema':1,'revision':7,'features':{'history':{'active':{HISTORY:old,error_key:error},'draft':{},'previous':{}}}})
    before = store.path.read_bytes()
    assert store.state()['states'][HISTORY]['active']['template'] == '{summary}{score}{ranking}'
    assert store.active(error_key)['template'] == error['template']
    assert store.path.read_bytes() == before and store.load()['revision'] == 7
    custom = deepcopy(old); custom['template'] = '我写的标题\n{content}'
    data = store.load(); data['features']['history']['active'][HISTORY] = custom
    store.atomic(store.path, data)
    assert store.active(HISTORY)['template'] == custom['template']
    hidden = deepcopy(old); hidden['visible'] = []
    assert templates.upgrade_spec(HISTORY, hidden) == hidden


def test_published_history_order_and_labels_reach_live_service_without_changing_onebot(store, monkeypatch):
    from hikari_bot.services import mycard, mycard_reports
    records = [{'usernamea':'demo','usernameb':'other','winner':'demo','pta':1012,'pta_ex':1000,'ptb':988,'ptb_ex':1000,
        'start_time':'2025-10-03T10:00:00.000Z','end_time':'2025-10-03T10:10:00.000Z'}]
    monkeypatch.setattr(mycard, 'mycard_get_records', AsyncMock(return_value=records))
    monkeypatch.setattr(mycard, 'fetch_previous_month_score', AsyncMock(return_value=1000))
    monkeypatch.setattr(mycard, 'fetch_player_history_rank', AsyncMock(return_value=12))
    publish(store, HISTORY, template='{ranking}\n{summary}{score}', mode='markdown')
    publish(store, SUMMARY, template='**选手** {user_id}\n赢面 {value7}% / 对局 {value4}')
    publish(store, 'history.mycard_reports_e0970980', template='名次 {rank}')
    async def run():
        before = await mycard_reports.history_report('demo',10,2025)
        with using_wording(present):
            actual = await mycard_reports.history_report('demo',10,2025)
        after = await mycard_reports.history_report('demo',10,2025)
        assert actual.text.mode == 'markdown'
        assert actual.text.startswith('名次 12\n**选手** demo')
        assert '赢面 100.00% / 对局 1' in actual.text and '结算分数：1012.00' in actual.text
        assert before.text == after.text and before.text.startswith('玩家：demo')
        assert actual.images
    asyncio.run(run())


def test_nested_price_fragments_respect_parent_order_and_escape_dynamic_data(store):
    from hikari_bot.qq_official.price_outputs import rarity_prompt
    from hikari_bot.features.card_prices.models import CardVersion
    publish(store,'price.prompt',template='{hint}\n{options}\n{heading}',mode='markdown')
    publish(store,'price.rarity_header',template='**卡名** {name}')
    text=rarity_prompt('[unsafe](https://example.invalid)',{'UR':[CardVersion(1,'QCCP-JP001','UR','name')]})
    assert text.startswith('回复编号') and '**卡名**' in text
    assert '](https' not in text and text.mode=='markdown'


def test_worker_messages_and_parser_text_have_editable_templates(store, tmp_path):
    from hikari_bot.services.link_parser import load_result
    root=tmp_path/'worker';root.mkdir()
    (root/'result.json').write_text(json.dumps([{'kind':'text','code':'audio_missing'},{'kind':'text','text':'外部标题'}]),encoding='utf-8')
    publish(store,'links.audio_missing',template='没有找到音轨。')
    publish(store,'links.parsed_text',template='解析结果：{parsed_text}',mode='markdown')
    with using_wording(present): result=load_result(root)
    assert result.media[0]=='没有找到音轨。'
    assert result.media[1]=='解析结果：外部标题' and result.media[1].mode=='markdown'


def test_history_navigation_separates_primary_parts_and_errors():
    states = [d for d in SCHEMA['states'].values() if d['feature'] == 'history']
    assert {category: sum(d['category'] == category for d in states)
            for category in ['main', 'part', 'error']} == {'main': 1, 'part': 5, 'error': 3}
    assert not SCHEMA['features']['history']['pending_work']
    assert SCHEMA['features']['members']['pending_work']


def test_manual_qq_preview_uses_same_composed_example_without_sending(store):
    from hikari_bot.qq_official.editor_preview import PreviewSession
    root = default(HISTORY)
    root.update(template='{ranking}\n{summary}{score}', mode='markdown')
    store.mutate('save', 'history', HISTORY, root, 0)
    session = PreviewSession(None, None, None, store=store)
    messages = asyncio.run(session.render(HISTORY, 'synthetic-user'))
    markdown = messages[0][0].data['markdown'].content
    assert '当前排名：86' in markdown and '11月场次：128' in markdown
    assert markdown.index('当前排名') < markdown.index('玩家')
    assert not store.queue.exists()
