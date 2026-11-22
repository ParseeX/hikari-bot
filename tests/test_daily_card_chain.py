"""每日一卡沿固定候选链恢复，未绑定用户也适用。"""
import asyncio
import hashlib
from unittest.mock import AsyncMock, Mock

from hikari_bot.services.daily_cards import DailyCards
from test_qq_migration import request


def test_unbound_command_uses_chain_and_reuses_result():
    from hikari_bot.qq_official.business import Queries
    catalog = Mock(random_id=Mock(side_effect=[11, 22]))
    image = AsyncMock(side_effect=[None, b'image', b'image'])
    state = Mock()
    state.binding.side_effect = AssertionError('每日一卡不能要求绑定')
    queries = Queries(catalog, image_loader=image, state=state)
    req = request(kind='group')
    result = asyncio.run(queries.handle(req, '每日一卡', 'ccb'))
    assert result.images == (b'image',) and not result.text
    result = asyncio.run(queries.handle(req, '每日一卡', ''))
    assert result.images == (b'image',)
    assert [c.args[0] for c in image.call_args_list] == [11, 22, 22]
    state.binding.assert_not_called()


def test_chain_retries_deterministically_and_remembers_success(tmp_path):
    req = request(kind='group', user='unbound-private-user', id='private-event')
    calls = []
    catalog = Mock(random_id=Mock(side_effect=[11, 22]))
    async def image(code):
        calls.append(code)
        return b'image' if code == 22 else None
    path = tmp_path / 'daily.sqlite3'
    draw = DailyCards(catalog, image, path=path)
    assert asyncio.run(draw.draw(req, day='2026-11-22')) == b'image'
    assert calls == [11, 22]
    first_seed = int.from_bytes(hashlib.sha256((req.actor.key+'_2026-11-22').encode()).digest()[:8], 'big')
    assert catalog.random_id.call_args_list[0].args == (first_seed,)
    calls.clear()
    # 模拟进程重启后，仍先返回同一张成功卡，不重新尝试第一张。
    draw = DailyCards(catalog, image, path=path)
    assert asyncio.run(draw.draw(req, day='2026-11-22')) == b'image'
    assert calls == [22]
    assert catalog.random_id.call_count == 2
    assert b'unbound-private-user' not in path.read_bytes()


def test_failed_chain_is_bounded_unique_and_repeatable(capsys):
    catalog = Mock(random_id=Mock(side_effect=lambda seed: seed))
    image = AsyncMock(return_value=None)
    req = request(kind='group', user='private-member', id='private-event')
    draw = DailyCards(catalog, image)
    assert asyncio.run(draw.draw(req, day='2026-11-22')) is None
    codes = [c.args[0] for c in image.call_args_list]
    assert len(codes) == len(set(codes)) == 5
    image.reset_mock()
    assert asyncio.run(draw.draw(req, day='2026-11-22')) is None
    assert [c.args[0] for c in image.call_args_list] == codes
    output = capsys.readouterr().out
    assert 'private-member' not in output and 'private-event' not in output
    assert 'exhausted' in output


def test_timeout_moves_to_next_candidate():
    catalog = Mock(random_id=Mock(side_effect=[11, 22]))
    async def image(code):
        if code == 11:
            await asyncio.sleep(1)
        return b'image'
    draw = DailyCards(catalog, image, card_timeout=.01)
    assert asyncio.run(draw.draw(request(), day='2026-11-22')) == b'image'
    assert catalog.random_id.call_count == 2


def test_duplicates_do_not_repeat_downloads_and_new_day_starts_fresh():
    image = AsyncMock(return_value=None)
    draw = DailyCards(Mock(random_id=Mock(return_value=11)), image)
    assert asyncio.run(draw.draw(request(), day='2026-11-22')) is None
    image.assert_awaited_once_with(11)
    image.reset_mock()
    image.return_value = b'image'
    assert asyncio.run(draw.draw(request(), day='2026-11-23')) == b'image'
    image.assert_awaited_once_with(11)


def test_total_deadline_and_cancellation_do_not_pin_a_failed_card(tmp_path):
    catalog = Mock(random_id=Mock(side_effect=lambda seed: seed))
    async def slow(code):
        await asyncio.sleep(10)
    path = tmp_path / 'daily.sqlite3'
    draw = DailyCards(catalog, slow, path=path, card_timeout=1, total_timeout=.01)
    assert asyncio.run(draw.draw(request(), day='2026-11-22')) is None
    assert not path.exists()

    async def cancel():
        task = asyncio.create_task(DailyCards(catalog, slow, path=path).draw(request()))
        await asyncio.sleep(.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError('外层取消必须向上传播')
    asyncio.run(cancel())
    assert not path.exists()
