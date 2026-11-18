"""按罕贵选择，再严格按卡片编号关联两种价格口径。"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
import unicodedata
from dataclasses import dataclass

from hikari_bot.features.cardrush.models import PriceSnapshot
from hikari_bot.features.cardrush.parsing import clean_card_name, rarity_jp_to_en
if TYPE_CHECKING:
    from hikari_bot.features.cardrush.service import CardrushService
from .client import JhsClient, JhsUnavailable
from .models import CardVersion, JhsPrice, normalized
from .identity import cardrush_identity
from .simplified import SimplifiedVersions


@dataclass(frozen=True)
class Comparison:
    version: CardVersion
    jhs: JhsPrice | None
    cardrush: tuple[PriceSnapshot, ...]
    cardrush_error: bool = False


def matches_version(record, name_jp, version):
    """报价与曲线共用完整编号、卡名及罕贵核对，禁止用盒号前缀代替。"""
    name, number = cardrush_identity(name_jp, version)
    return (version.game_sub_key == 'ocg' and bool(version.number)
            and normalized(record.model_number or '') == normalized(number)
            and clean_card_name(record.name) == clean_card_name(name)
            and rarity_jp_to_en(record.rarity or '').upper() == version.rarity.upper())


def group_rarities(versions: list[CardVersion]) -> dict[str, list[CardVersion]]:
    groups: dict[str, list[CardVersion]] = {}
    seen: set[int] = set()
    for version in sorted(versions, key=lambda v: (v.selection_group, v.number, v.rarity, v.id)):
        if version.id not in seen:
            groups.setdefault(version.selection_group, []).append(version)
            seen.add(version.id)
    return groups


class ComparisonService:
    def __init__(self, jhs: JhsClient, cardrush: CardrushService, version_catalog=None):
        self.jhs = jhs
        self.cardrush = cardrush
        self.version_catalog = version_catalog
        self.simplified = SimplifiedVersions(jhs)

    async def versions(self, name_jp: str, rarity: str | None = None,
                       model_prefix: str | None = None,
                       names_cn: tuple[str, ...] = (), *, catalog_id: int | None = None,
                       include_simplified: bool = False) -> list[CardVersion]:
        async def discover():
            return await self.online_versions(name_jp, names_cn)
        versions = (await self.version_catalog.get(catalog_id, name_jp, self.jhs, discover)
                    if self.version_catalog is not None and catalog_id is not None else await discover())
        if include_simplified:
            versions = versions + await self.simplified.get(name_jp, names_cn, versions)
        # 完整列表先落库，再按本次罕贵和编号筛选，不能把筛选结果标为完整。
        return [v for v in versions if (not rarity or v.selection_group == rarity.upper())
                and (not model_prefix or v.number.upper().startswith(model_prefix.upper() + '-'))]

    async def online_versions(self, name_jp, names_cn):
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
                if not v.name_jp or normalized(v.name_jp) == normalized(name_jp)]

    async def compare(self, name_jp: str, versions: list[CardVersion], *, japanese: bool = False) -> list[Comparison]:
        # 先查编号再核对名称和罕贵，避免 LIKE 查询混入其他卡包或相似名称。
        async def local(version: CardVersion):
            if version.game_sub_key == 'sc' or not version.number:
                return ()
            _, number = cardrush_identity(name_jp, version)
            records = await self.cardrush.search_prices("", None, number, 200)
            return tuple(r for r in records if matches_version(r, name_jp, version))

        async def domestic():
            if japanese:
                return await self.jhs.japanese_prices([v for v in versions if v.game_sub_key == 'ocg'])
            result = {}
            for partition in ('ocg', 'sc'):
                ids = [v.id for v in versions if v.game_sub_key == partition]
                if ids:
                    try:
                        options = {'game_sub_key': partition} if partition == 'sc' else {}
                        result.update(await self.jhs.prices(ids, **options))
                    except JhsUnavailable:
                        pass  # 一个分区失败不覆盖另一分区的成功报价。
            return result

        values = await asyncio.gather(
            domestic(),
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
