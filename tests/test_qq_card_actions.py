HINT = '> 卡名识别有误？可使用 添加别名 卡名 别名 指令为卡片添加别名！'
from hikari_bot.qq_official.output_templates import notice_text
HINT_MD = notice_text('common.card_name_hint', markdown=True)
import asyncio
import copy
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_qq_preview import INFO, NOW, bot, event
from nonebot.adapters.qq import Bot
from hikari_bot.qq_official.card_actions import CardActions, card_message, escape_markdown
from hikari_bot.qq_official.config import PilotConfig
from hikari_bot.qq_official.delivery import ReplyPager
from hikari_bot.qq_official.prices import PriceQueries, IsolatedVersionCache
from hikari_bot.features.card_prices.models import CardVersion, JhsPrice
from hikari_bot.features.card_prices.service import Comparison
from hikari_bot.features.cardrush.models import PriceSnapshot
from hikari_bot.features.cardrush.read_only import ReadOnlyPrices
from hikari_bot.services import card_art

CONFIG = PilotConfig('test', 'fake', users=frozenset({'only-user'}))


class CardTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_effect_plain_message_uses_original_layout_without_markdown_markers(self):
        from nonebot.adapters.qq.event import GroupMessageCreateEvent
        from hikari_bot.qq_official.media import NamedFileBot
        from hikari_bot.services.card_text import format_card_effect
        info = copy.deepcopy(INFO)
        info['text']['desc'] = '①：保留原文中的 * 和 #。\n②：完整的第二段。'
        catalog = Mock(search=Mock(return_value=info))
        config = PilotConfig('synthetic-app', 'fake', groups=frozenset({'synthetic-group'}))
        for at in (True, False):
            client = bot()
            async def post(**kwargs):
                return await Bot.post_group_messages(client, **kwargs)
            async def group(**kwargs):
                return await Bot.send_to_group(client, **kwargs)
            client.post_group_messages, client.send_to_group = post, group
            source = event('group')
            if not at:
                source = GroupMessageCreateEvent.model_validate(source.model_dump())
            from hikari_bot.qq_official.output_templates import default, draft
            spec = default('effect.card')
            spec.update(template='{body}', mode='text')
            with draft('effect.card', spec):
                await CardActions(catalog).handle('效果', '卡名', config, NamedFileBot(client), source, ReplyPager(now=lambda: NOW))
            payload = client._request.call_args.args[0].json
            self.assertEqual(payload['msg_type'], 0)
            self.assertEqual(payload['content'], format_card_effect(info) + '\n\n' + HINT)
            self.assertNotIn('keyboard', payload)

    async def test_long_private_effect_uses_clean_text_without_losing_any_effect(self):
        from hikari_bot.services.card_text import format_card_effect
        info = copy.deepcopy(INFO)
        info['text']['desc'] = '①：完整长效果。' * 350
        client = bot()
        from hikari_bot.qq_official.output_templates import default, draft
        spec = default('effect.card')
        spec.update(template='{body}', mode='text')
        with draft('effect.card', spec):
            await CardActions(Mock(search=Mock(return_value=info))).handle(
                '效果', '卡名', CONFIG, client, event(), ReplyPager(now=lambda: NOW))
        payloads = [call.args[0].json for call in client._request.call_args_list]
        self.assertTrue(all(p['msg_type'] == 0 for p in payloads))
        self.assertEqual(''.join(p['content'] for p in payloads), format_card_effect(info) + '\n\n' + HINT)

    def test_reading_escape_and_stable_button_identity(self):
        info = copy.deepcopy(INFO)
        info.update(jp_ruby='はるうらら', passcode='01455812')
        info['cn_name'] = '[title](https://example.invalid)'
        info['text']['desc'] = '*literal* [x](url) <tag> _name_\\text\n完整末尾'
        rich, plain, content = card_message(info, 'only-user', images=True, prices=True)
        self.assertIn('> はるうらら\n> 灰流うらら\n\n', content)
        self.assertNotIn('## 效果', content)
        self.assertIn(escape_markdown(info['text']['desc']), content)
        self.assertNotIn('](https', content)
        self.assertTrue(plain.endswith(info['text']['desc']))
        buttons = rich['keyboard'][0].data['keyboard'].content.rows[0].buttons
        self.assertEqual([b.render_data.label for b in buttons], ['查看卡图', '查询卡价'])
        self.assertEqual([b.action.data for b in buttons], ['卡图 01455812', '卡价 01455812'])
        self.assertTrue(all(b.action.permission.type == 2 for b in buttons))

    def test_missing_names_readings_and_unavailable_backends(self):
        for info in ({'en_name': 'English', 'jp_ruby': 'unused'}, {}, {'jp_name': '日本語'}):
            rich, _, content = card_message(info, 'only-user')
            self.assertNotIn('unused', content)
            self.assertNotIn('（）', content)
            if info.get('jp_name'):
                self.assertIn('> 日本語\n\n', content)
            else:
                self.assertNotIn('> ', content.removesuffix('\n\n' + HINT_MD))
            self.assertFalse(rich['keyboard'])
        rich, _, _ = card_message({'id': 0}, 'only-user', images=True, prices=True)
        self.assertFalse(rich['keyboard'])

    def test_reading_quote_escapes_content_and_plain_mode_has_no_quote(self):
        from hikari_bot.qq_official.output_templates import default, draft
        from hikari_bot.qq_official.card_actions import card_text
        info = copy.deepcopy(INFO)
        info['jp_ruby'] = '*よみ*\nつづき'
        content = card_text(info)
        self.assertIn('> \\*よみ\\*\n> つづき\n> 灰流うらら\n\n', content)
        spec = default('effect.card')
        spec.update(template='{reading}{original}\n{effect}', mode='text')
        with draft('effect.card', spec):
            self.assertEqual(card_text(info), '*よみ*\nつづき\n灰流うらら\n'+info['text']['desc'] + '\n\n' + HINT)

    def test_existing_official_name_template_remains_compatible(self):
        from hikari_bot.qq_official.output_templates import default, draft
        from hikari_bot.qq_official.card_actions import card_text
        info = copy.deepcopy(INFO)
        info['jp_ruby'] = 'はるうらら'
        spec = default('effect.card')
        spec.update(template='{official}', mode='markdown')
        with draft('effect.card', spec):
            self.assertEqual(card_text(info), '灰流うらら（はるうらら）' + '\n\n' + HINT_MD)

    def test_pendulum_link_and_long_text_preserved(self):
        info = copy.deepcopy(INFO)
        info['text'] = {'types': '[怪兽|链接] 2300/LINK-3 [↙↓↘]', 'pdesc': '灵摆完整\n第二行', 'desc': '怪兽完整'*2000}
        _, plain, content = card_message(info, 'only-user')
        self.assertIn(escape_markdown(info['text']['types']), content)
        self.assertIn('灵摆完整\n第二行\n\n***\n\n', content)
        self.assertNotIn('## 灵摆效果', content)
        self.assertNotIn('## 效果', content)
        self.assertTrue(content.endswith('怪兽完整'*2000 + '\n\n' + HINT_MD))
        self.assertTrue(plain.endswith('怪兽完整'*2000))

    async def test_effect_resolves_once_using_existing_search_semantics(self):
        catalog = Mock()
        catalog.search.return_value = INFO
        catalog.search_many.return_value = [INFO, {'id': 999}]
        client = bot()
        await CardActions(catalog, image_loader=AsyncMock()).handle('效果', '模糊片段', CONFIG, client, event(), ReplyPager(now=lambda: NOW))
        catalog.search.assert_called_once_with('模糊片段', fuzzy=True)
        catalog.search_many.assert_not_called()
        payload = client._request.call_args.args[0].json
        self.assertEqual(payload['msg_type'], 2)
        self.assertEqual(payload['keyboard']['content']['rows'][0]['buttons'][0]['action']['data'], '卡图 14558127')

    async def test_no_group_or_other_user_query(self):
        catalog = Mock()
        for source in (event('group'), event(user='other')):
            self.assertFalse(await CardActions(catalog).handle('效果', 'test', CONFIG, bot(), source, ReplyPager()))
        catalog.search.assert_not_called()

    async def test_explicit_image_page_keeps_existing_navigation(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (10, 10), 'blue').save(buffer, format='PNG')
        client = bot()
        client._extract_qq_media = Bot._extract_qq_media
        client.post_c2c_files = AsyncMock(return_value=SimpleNamespace(file_info='synthetic-upload'))
        catalog = Mock()
        catalog.search.return_value = INFO
        catalog.image_ids.return_value = [14558127, 14558128, 14558129]
        loader = AsyncMock(return_value=buffer.getvalue())
        await CardActions(catalog, image_loader=loader).handle('卡图', '14558127 0', CONFIG, client, event(), ReplyPager(now=lambda: NOW))
        self.assertEqual(loader.call_count, 1)
        self.assertEqual(client.post_c2c_files.call_count, 1)
        self.assertFalse(client.post_c2c_files.call_args.kwargs['srv_send_msg'])
        self.assertEqual([c.args[0].json['msg_type'] for c in client._request.call_args_list], [7, 2])
        self.assertEqual([c.args[0].json['msg_seq'] for c in client._request.call_args_list], [1, 2])
        buttons = client._request.call_args.args[0].json['keyboard']['content']['rows']
        self.assertIn('卡图 14558127 1', str(buttons))

    async def test_art_alternate_order_and_failure_slots(self):
        catalog = Mock()
        catalog.image_ids.return_value = [10, 11, 12]
        loader = AsyncMock(side_effect=[b'a', None, b'c'])
        with patch.object(card_art, 'normalize_card_art', side_effect=lambda v: v):
            images = await card_art.get_card_images(catalog, '10', image_loader=loader)
        self.assertEqual(images, [(10, b'a'), (11, None), (12, b'c')])


class PriceTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_query_owns_selection_session(self):
        first = asyncio.Event()
        entered = asyncio.Event()
        async def versions(name, *args, **kwargs):
            if name == 'first':
                entered.set()
                await first.wait()
            return [CardVersion(1, 'A-1', 'N', name)]
        backend = PriceQueries(Mock(), Mock(), None)
        backend.comparison = Mock(versions=versions)
        old = asyncio.create_task(backend.start({'jp_name':'first'}, 'u'))
        await entered.wait()
        await backend.start({'jp_name':'second'}, 'u')
        first.set()
        self.assertEqual('', await old)
        self.assertEqual(backend.sessions['u'][1], 'second')

    async def test_price_button_command_and_next_message_reach_real_backend(self):
        backend = PriceQueries(Mock(), Mock(), None)
        version = CardVersion(1, 'A-1', 'N', INFO['jp_name'])
        backend.comparison = Mock(versions=AsyncMock(return_value=[version]),
            compare=AsyncMock(return_value=[Comparison(version, JhsPrice(1, Decimal('2'), None, None), ())]))
        catalog = Mock()
        catalog.search.return_value = INFO
        actions = CardActions(catalog, price_backend=backend)
        from hikari_bot.qq_official.media import NamedFileBot
        client, pager = NamedFileBot(bot()), ReplyPager(now=lambda: NOW)
        await actions.handle('卡价', '14558127', CONFIG, client, event(), pager)
        self.assertIn('## ', client._request.call_args.args[0].json['markdown']['content'])
        source = event()
        source.id = 'synthetic-selection'
        await actions.handle('1', '', CONFIG, client, source, pager)
        self.assertIn('**2.00 元**', client._request.call_args.args[0].json['markdown']['content'])
        self.assertNotIn(HINT, client._request.call_args.args[0].json['markdown']['content'])
        self.assertEqual(client._request.call_args.args[0].json['markdown']['content'].count(HINT), 0)
        catalog.search.assert_called_once_with('14558127', fuzzy=True)

    async def test_rarity_currency_exact_version_and_expiry(self):
        clock = [0]
        jhs, rush = Mock(), Mock()
        jhs.versions = AsyncMock(return_value=[CardVersion(1, 'ABC-001', 'N', INFO['jp_name'])])
        jhs.prices = AsyncMock(return_value={1: JhsPrice(1, Decimal('1.20'), Decimal('1.50'), None)})
        rush.search_prices = AsyncMock(return_value=[PriceSnapshot(7, INFO['jp_name'], 'ノーマル', 'ABC-001', 200, '2026-10-13'),
            PriceSnapshot(8, 'wrong-card', 'ノーマル', 'ABC-001', 9999, '2026-10-13')])
        backend = PriceQueries(jhs, rush, None, clock=lambda: clock[0])
        self.assertNotIn('ABC-001', await backend.start(INFO, 'only-user'))
        self.assertEqual(backend.selection_options('only-user'), (1, ('N',)))
        self.assertIn('请先发送', await backend.choose('1', 'other'))
        result = await backend.choose('1', 'only-user')
        self.assertIn('1.20 元', result)
        self.assertIn('200 円', result)
        self.assertNotIn('9999', result)
        await backend.start(INFO, 'only-user')
        clock[0] = 181
        self.assertIn('已过期', await backend.choose('1', 'only-user'))
        self.assertFalse(backend.active('only-user'))

    async def test_japanese_mode_cancel_and_invalid_choice(self):
        backend = PriceQueries(Mock(), Mock(), None)
        version = CardVersion(1, 'A-1', 'SR', INFO['jp_name'])
        backend.comparison = Mock(versions=AsyncMock(return_value=[version]), compare=AsyncMock(return_value=[Comparison(version, None, ())]))
        await backend.start(INFO, 'u', japanese=True)
        self.assertIn('请回复', await backend.choose('bogus', 'u'))
        self.assertIn('日版最低价', await backend.choose('SR', 'u'))
        backend.comparison.compare.assert_awaited_once_with(INFO['jp_name'], [version], japanese=True)
        await backend.start(INFO, 'u')
        self.assertIn('已取消', await backend.choose('取消', 'u'))

    async def test_cardrush_read_only_latest_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'prices.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE card_price_history (id INTEGER,product_id INTEGER,name TEXT,rarity TEXT,model_number TEXT,price INTEGER,changed_at TEXT)')
                db.executemany('INSERT INTO card_price_history VALUES (?,?,?,?,?,?,?)', [(1,9,'n','N','A-1',100,'2026-10-12'),(2,9,'n','N','A-1',200,'2026-10-13')])
            db.close()
            before = hashlib.sha256(path.read_bytes()).digest()
            reader = ReadOnlyPrices(path)
            self.assertEqual((await reader.search_prices('n'))[0].price, 200)
            with reader._connect() as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute('DELETE FROM card_price_history')
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    async def test_version_cache_writes_only_independent_database(self):
        from contextlib import contextmanager
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp)/'cards.sqlite3', Path(tmp)/'srbs/versions.sqlite3'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE card_names(card_id INTEGER,language TEXT,normalized TEXT)')
                db.execute('CREATE TABLE jhs_cards(card_id INTEGER,jhs_card_id INTEGER)')
                db.execute("INSERT INTO card_names VALUES(1,'ja','name')")
                db.execute('INSERT INTO jhs_cards VALUES(1,2)')
            db.close()
            @contextmanager
            def connect():
                db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
                try: yield db
                finally: db.close()
            before=hashlib.sha256(source.read_bytes()).digest()
            cache=IsolatedVersionCache(dest,SimpleNamespace(path=source,connect=connect))
            payload={'card_id':2,'complete':True,'versions':[{'id':3,'card_id':2,'object_type':'card','name_jp':'name',
                'rarity':'N','number':'A-1','packs':[{'id':4,'name':'pack'}]}]}
            values=cache.save(1,'name',[2],[2],[payload])
            self.assertEqual(values,cache.load(1,'name')[1])
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
            self.assertTrue(dest.is_file())


if __name__ == '__main__':
    unittest.main()
