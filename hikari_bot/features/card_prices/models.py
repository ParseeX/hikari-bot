from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import unicodedata


def normalized(value: str) -> str:
    """仅用于比对；请求上游时保留日文原名。"""
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def money(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("无效价格") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("无效价格")
    return result


@dataclass(frozen=True)
class CardVersion:
    id: int
    number: str
    rarity: str
    name_jp: str = ""
    name_cn: str = ""
    card_id: int | None = None
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: dict) -> "CardVersion":
        version = cls(int(value["id"]), str(value.get("number") or ""),
                      str(value["rarity"]).strip().upper(),
                      str(value.get("name_jp") or ""), str(value.get("name_cn") or ""),
                      int(value["card_id"]) if value.get("card_id") else None,
                      tuple(str(n) for n in value.get("aliases", [])))
        if version.id <= 0 or not version.rarity:
            raise ValueError("无效卡片版本")
        return version


@dataclass(frozen=True)
class JhsPrice:
    id: int
    minimum: Decimal | None
    market: Decimal | None
    market_date: str | None
    error: str | None = None

    @classmethod
    def from_dict(cls, value: dict) -> "JhsPrice":
        return cls(int(value["id"]), money(value.get("min_price")),
                   money(value.get("market_price")), value.get("market_date"),
                   "暂时不可用" if value.get("error") else None)
