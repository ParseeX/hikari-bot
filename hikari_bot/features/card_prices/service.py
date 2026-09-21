"""按罕贵选择，再严格按卡片编号关联两种价格口径。"""
import asyncio
import unicodedata
from dataclasses import dataclass

from hikari_bot.features.cardrush.models import PriceSnapshot
from hikari_bot.features.cardrush.parsing import clean_card_name, rarity_jp_to_en
from hikari_bot.features.cardrush.service import CardrushService
from .client import JhsClient, JhsUnavailable
from .models import CardVersion, JhsPrice, normalized


@dataclass(frozen=True)
class Comparison:
    version: CardVersion
    jhs: JhsPrice | None
    cardrush: tuple[PriceSnapshot, ...]
    cardrush_error: bool = False


def group_rarities(versions: list[CardVersion]) -> dict[str, list[CardVersion]]:
    groups: dict[str, list[CardVersion]] = {}
    seen: set[int] = set()
    for version in sorted(versions, key=lambda v: (v.rarity, v.number, v.id)):
        if version.id not in seen:
            groups.setdefault(version.rarity, []).append(version)
            seen.add(version.id)
    return groups


class ComparisonService:
    def __init__(self, jhs: JhsClient, cardrush: CardrushService):
        self.jhs = jhs
        self.cardrush = cardrush

    async def versions(self, name_jp: str, rarity: str | None = None,
                       model_prefix: str | None = None,
                       names_cn: tuple[str, ...] = ()) -> list[CardVersion]:
        versions = await self.jhs.versions(name_jp)
        # 上游不能总是识别日文原名中的全角英数字/符号；原文无结果才补查等价写法。
        compatible_name = unicodedata.normalize("NFKC", name_jp)
        if not versions and compatible_name != name_jp:
            versions = await self.jhs.versions(compatible_name)
        aliases = {normalized(n) for n in names_cn if n}
        if aliases:
            # 集换社通常不返回日文名，用卡片库提供的中文正式名/别名核对。
            versions = [v for v in versions if (
                normalized(v.name_jp) == normalized(name_jp) if v.name_jp else
                bool(aliases & {normalized(n) for n in (v.name_cn, *v.aliases)}))]
        elif len({v.card_id for v in versions if not v.name_jp}) > 1:
            raise JhsUnavailable("搜索返回多张卡片，无法确认对应关系")
        return [v for v in versions
                if (not v.name_jp or normalized(v.name_jp) == normalized(name_jp))
                and (not rarity or v.rarity == rarity.upper())
                and (not model_prefix or v.number.upper().startswith(model_prefix.upper() + "-"))]

    async def compare(self, name_jp: str, versions: list[CardVersion], *, japanese: bool = False) -> list[Comparison]:
        # 先查编号再核对名称和罕贵，避免 LIKE 查询混入其他卡包或相似名称。
        async def local(version: CardVersion):
            if not version.number:
                return ()
            records = await self.cardrush.search_prices("", None, version.number, 200)
            return tuple(r for r in records
                         if normalized(r.model_number or "") == normalized(version.number)
                         and clean_card_name(r.name) == clean_card_name(name_jp)
                         and rarity_jp_to_en(r.rarity or "").upper() == version.rarity)

        values = await asyncio.gather(
            self.jhs.japanese_prices(versions) if japanese else self.jhs.prices([v.id for v in versions]),
            *(local(v) for v in versions), return_exceptions=True,
        )
        jhs = values[0] if isinstance(values[0], dict) else {}
        return [Comparison(v, jhs.get(v.id),
                           () if isinstance(records, BaseException) else records,
                           isinstance(records, BaseException))
                for v, records in zip(versions, values[1:])]


def rarity_prompt(name_jp: str, groups: dict[str, list[CardVersion]]) -> str:
    lines = [f"【{name_jp}】请选择罕贵："]
    for index, (rarity, versions) in enumerate(groups.items(), 1):
        numbers = "、".join(dict.fromkeys(v.number or "编号未知" for v in versions))
        lines.append(f"{index}. {rarity}（{numbers}）")
    return "\n".join(lines) + "\n回复编号或罕贵名称；回复“取消”结束。"


def format_comparison(name_jp: str, rarity: str, rows: list[Comparison], *, japanese: bool = False) -> list[str]:
    header = f"【{name_jp}｜{rarity}】\n"
    pages: list[str] = []
    text = header
    for row in rows:
        lines = [row.version.number or "编号未知"]
        if row.jhs is None or row.jhs.error:
            lines.append("日版最低价：暂时不可用" if japanese else "集换社：暂时不可用")
        elif japanese:
            value = f'{row.jhs.minimum:.2f} 元' if row.jhs.minimum is not None else '未找到符合备注的在售出品'
            lines.append(f'日版最低价：{value}')
        else:
            def yuan(value):
                return f"{value:.2f} 元" if value is not None else "暂无数据"
            lines.append(f"集换社最低价：{yuan(row.jhs.minimum)}")
            lines.append(f"集换价：{yuan(row.jhs.market)}")
        if row.cardrush_error:
            lines.append("Cardrush 买取价：暂时不可用")
        elif not row.cardrush:
            lines.append("Cardrush 买取价：数据库暂无对应记录")
        else:
            for record in row.cardrush:
                lines.append(f"Cardrush 买取价：{record.price:,} 円")
        block = "\n".join(lines) + "\n\n"
        if len(text) + len(block) > 2800 and text != header:
            pages.append(text.rstrip())
            text = header
        text += block
    if text != header:
        pages.append(text.rstrip())
    return pages
