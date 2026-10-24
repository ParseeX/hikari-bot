import asyncio
import io
import json
from types import SimpleNamespace

from PIL import Image

from hikari_bot.services import ygodeck
from hikari_bot.services.mycard_archive.queries import player_image
from hikari_bot.services.mycard_archive.replay import Deck


def test_query_image_passes_sorted_sections_and_only_id_and_classification(tmp_path, monkeypatch):
    main = (123,123,123,456) + tuple(range(1000,1056))
    version = SimpleNamespace(deck=Deck('水桥',main,(789,),(999,)))
    path = tmp_path/'deck-rules.json'
    path.write_text(json.dumps({'version':1,'rules':[
        {'label':'黯蜜','cards':[123]}, {'label':'黑魔导','cards':[456]},
    ]}), encoding='utf-8')
    calls = []
    async def render(ydk, player, match, result='', deck_name=''):
        calls.append((ydk,player,match,result,deck_name))
        return Image.new('RGB',(10,10),'white')
    monkeypatch.setattr(ygodeck,'generate_deck_image',render)
    data = asyncio.run(player_image('水桥',version,path))
    assert data.startswith(b'\xff\xd8')
    with Image.open(io.BytesIO(data)) as output:
        assert output.size == (6, 6)
    ydk, player, match, result, name = calls[0]
    assert (player,match,result,name) == ('水桥','','','60黑魔导黯蜜')
    sections = ygodeck.parse_ydk(ydk)
    assert len(sections[0]) == 60 and sections[1:] == (['789'],['999'])


def test_renderer_preserves_missing_card_position_and_draws_two_information_lines(monkeypatch):
    async def load(card_ids, **kwargs):
        assert kwargs == {'size':(200,290),'preserve_positions':True}
        assert card_ids == ['1','2','3']
        return [None,Image.new('RGB',(200,290),'red'),Image.new('RGB',(200,290),'blue')]
    monkeypatch.setattr(ygodeck,'batch_get_images',load)
    drawn=[]
    original=ygodeck.ImageDraw.ImageDraw.text
    def text(self,xy,value,*args,**kwargs):
        drawn.append(value)
        return original(self,xy,value,*args,**kwargs)
    monkeypatch.setattr(ygodeck.ImageDraw.ImageDraw,'text',text)
    image=asyncio.run(ygodeck.generate_deck_image('#main\n1\n#extra\n2\n!side\n3','水桥','',deck_name='黑魔导黯蜜'))
    assert image.getpixel((40,140)) == (238,238,238)
    assert image.getpixel((40,545)) == (255,0,0)
    assert image.getpixel((40,1245)) == (0,0,255)
    assert drawn[-2:] == ['黑魔导黯蜜','水桥']
    assert ' ' not in drawn
    image.close()


def test_batch_keeps_missing_slots_when_requested(monkeypatch):
    buffer=io.BytesIO()
    Image.new('RGB',(20,20),'red').save(buffer,format='PNG')
    async def download(code):
        return None if code=='missing' else buffer.getvalue()
    monkeypatch.setattr(ygodeck,'get_ygopic',download)
    images=asyncio.run(ygodeck.batch_get_images(['missing','ok'],size=(200,290),preserve_positions=True))
    assert images[0] is None and images[1].size==(200,290)
    images[1].close()


def test_cached_batch_reuses_duplicates_without_artificial_waits(tmp_path, monkeypatch):
    from hikari_bot.services import ygocard

    for code, color in [(1, 'red'), (2, 'blue')]:
        image = Image.new('RGB', (200, 290), color)
        image.paste('white', (20, 20, 60, 60))
        image.save(tmp_path/f'{code}.jpg')
    monkeypatch.setattr(ygocard, 'CARD_PICS', str(tmp_path))
    calls, waits = [], []

    async def cached(code):
        calls.append(code)
        return await ygocard.get_ygopic(code)

    async def sleep(delay):
        waits.append(delay)

    def network(*args, **kwargs):
        raise AssertionError('缓存命中不应下载卡图')

    monkeypatch.setattr(ygodeck, 'get_ygopic', cached)
    monkeypatch.setattr(ygodeck.asyncio, 'sleep', sleep)
    monkeypatch.setattr(ygocard.aiohttp, 'ClientSession', network)
    images = asyncio.run(ygodeck.batch_get_images(['1','2','1','2','1','2']))
    assert waits == []
    assert calls == ['1','2']
    assert len(images) == 6
    assert [img.getpixel((0,0)) for img in images] == [(254,0,0),(0,0,254)]*3
    for img in images:
        img.close()


def test_batch_bounds_concurrency_and_retains_order_on_failure(monkeypatch):
    buffer = io.BytesIO()
    Image.new('RGB', (20,20), 'red').save(buffer, format='PNG')
    active, peak = 0, 0

    async def download(code):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            if code == 'bad':
                return b'invalid image'
            return buffer.getvalue()
        finally:
            active -= 1

    async def log(message):
        pass

    monkeypatch.setattr(ygodeck, 'get_ygopic', download)
    monkeypatch.setattr(ygodeck, 'log_message', log)
    images = asyncio.run(ygodeck.batch_get_images(
        ['1','bad','2','3','4','bad','1'], preserve_positions=True))
    assert 1 < peak <= 3
    assert [img is None for img in images] == [False,True,False,False,False,True,False]
    for img in images:
        if img is not None:
            img.close()


def test_custom_information_stays_within_the_image(monkeypatch):
    async def load(card_ids, **kwargs):
        return [Image.new('RGB', (200, 290), 'red') for _ in card_ids]
    monkeypatch.setattr(ygodeck, 'batch_get_images', load)
    drawn = []
    original = ygodeck.ImageDraw.ImageDraw.text
    def text(self, xy, value, *args, **kwargs):
        if value in [player, name, event + ' ' + result]:
            drawn.append((xy, value, kwargs['font'].getlength(value)))
        return original(self, xy, value, *args, **kwargs)
    player, name, event, result = '玩家' * 20, '卡组' * 40, '赛事' * 30, '冠军' * 15
    monkeypatch.setattr(ygodeck.ImageDraw.ImageDraw, 'text', text)
    image = asyncio.run(ygodeck.generate_deck_image(
        '#main\n1\n#extra\n2\n!side\n3', player, event, result=result, deck_name=name))
    assert len(drawn) == 3
    assert all(xy[0] + width <= image.width - 30 for xy, _, width in drawn)
    assert all(0 < xy[1] < image.height for xy, _, _ in drawn)
    image.close()
