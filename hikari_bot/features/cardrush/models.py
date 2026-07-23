from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PriceRecord:
    product_id: int
    name: str
    price: int
    rarity: str | None
    model_number: str | None
    updated_at: str | None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PriceRecord":
        return cls(
            product_id=int(value["product_id"]),
            name=str(value["name"]),
            price=int(value["price"]),
            rarity=value.get("rarity"),
            model_number=value.get("model_number"),
            updated_at=value.get("updated_at"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PriceSnapshot:
    product_id: int
    name: str
    rarity: str | None
    model_number: str | None
    price: int
    changed_at: str


@dataclass(frozen=True)
class PricePoint:
    price: int
    changed_at: str


@dataclass(frozen=True)
class PriceChange:
    product_id: int
    name: str
    rarity: str | None
    model_number: str | None
    old_price: int | None
    new_price: int
    change_type: str
    price_diff: int | None
    percent_diff: float | None
    changed_at: str
