"""复用既有卡组图片/PDF 排版，只接收明确的主库与图片加载器。"""
import asyncio
import io

from .platform import Result


class DeckRenderer:
    def __init__(self, catalog, image_loader):
        self.catalog, self.image_loader = catalog, image_loader
        self.lock = asyncio.Lock()

    async def lookup(self, code):
        return await asyncio.to_thread(self.catalog.by_id, code)

    async def card_list(self, cards):
        from .ygodeck import generate_card_list_image
        async with self.lock:
            loaded = await self.load_images(cards)
            if any(not value for value in loaded.values()):
                raise ValueError('部分卡图暂时无法获取，请稍后重试；未发送缺卡的计算结果。')
            async def cached(code):
                return loaded[code]
            image = await generate_card_list_image(cards, image_loader=cached)
            return self.encode(image)

    async def load_images(self, codes):
        loaded = {}
        semaphore = asyncio.Semaphore(3)
        async def load(code):
            async with semaphore:
                try:
                    loaded[code] = await asyncio.wait_for(self.image_loader(code), timeout=35)
                except Exception:
                    loaded[code] = None
        await asyncio.gather(*(load(code) for code in set(codes)))
        return loaded

    @staticmethod
    def encode(image):
        with image, io.BytesIO() as buffer:
            image.save(buffer, format='PNG')
            return buffer.getvalue()

    async def render(self, command, argument):
        from . import ygodeck
        if len(argument) > 16000:
            return Result('卡组内容过长。')
        if argument.startswith('#main'):
            text, metadata = argument, []
        else:
            parts = argument.split()
            if not parts or not ygodeck.is_deck_url(parts[0]):
                return Result('用法：卡组图片 卡组分享链接 [玩家ID 比赛名 卡组名 成绩]；卡组PDF 卡组分享链接')
            try:
                text = ygodeck.get_deck_text_from_url(parts[0])
            except (ValueError, IndexError, KeyError):
                return Result('卡组链接无效。')
            metadata = parts[1:5]
        sections = ygodeck.parse_ydk(text)
        if not sections[0] or len(sections[0]) > 60 or any(len(s) > 15 for s in sections[1:]):
            return Result('卡组数量无效：主卡组最多60张，额外和副卡组各最多15张。')
        for code in set(sum((list(s) for s in sections), [])):
            if not code.isascii() or not code.isdigit() or not await self.lookup(code):
                return Result('卡组包含资料库中不存在的卡密。')
        async with self.lock:
            if command == '卡组PDF':
                categories = [0, 0, 0]
                for code in set(sections[0]):
                    info = await self.lookup(code)
                    types = info.get('text', {}).get('types', '')
                    category = 1 if '魔法' in types else 2 if '陷阱' in types else 0
                    categories[category] += 1
                if max(categories) > 20:
                    return Result('当前PDF模板每类最多20种主卡。此卡组超出模板容量，请使用卡组图片；未生成会漏卡或重叠的PDF。')
                buffer = await ygodeck.generate_deck_list_pdf(text, card_lookup=self.lookup)
                return Result(file=buffer.getvalue(), filename='deck.pdf')
            loaded = await self.load_images(sum((list(s) for s in sections), []))
            missing = sum(not value for value in loaded.values())
            if missing:
                return Result(f'有{missing}种卡片的图片暂时无法获取，请稍后重试；未发送缺卡的卡组图片。')
            async def cached(code):
                return loaded[code]
            player, match, name, result = (metadata + [''] * 4)[:4]
            image = await ygodeck.generate_deck_image(text, player, match, result, name,
                                                     image_loader=cached)
            return Result(images=(self.encode(image),))
