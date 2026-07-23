import re

from hikari_bot.services.ygocard import get_card_info

RARITY_MAPPING = {
    "ノーマル": "N",
    "レア": "R",
    "スーパー": "SR",
    "ウルトラ": "UR",
    "レリーフ": "UTR",
    "コレクターズ": "CR",
    "プレミアムゴールド": "GR",
    "ホログラフィック": "HR",
    "シークレット": "SER",
    "エクストラシークレット": "ESR",
    "プリズマティックシークレット": "PSER",
    "クォーターセンチュリーシークレット": "QCSER",
    "20thシークレット": "20SER",
    "ゴールドシークレット": "GSER",
    "10000シークレット": "10000SER",
    "ノーマルパラレル": "NPR",
    "ウルトラパラレル": "UPR",
    "ホログラフィックパラレル": "HPR",
    "シークレットパラレル": "SEPR",
    "ウルトラシークレット": "USR",
    "KCウルトラ": "UKC",
    "シークレットSPECIALREDVer.": "SER-SRV",
    "ウルトラブルー": "UR",
    "ウルトラレッド": "UR",
    "ウルトラSPECIALPURPLEVer.": "UR",
    "ウルトラSPECIALILLUSTVer.": "UR",
    "クォーターセンチュリーシークレットGREEN Ver.": "QCSER",
    "OFウルトラ": "UR-OF",
    "OFプリズマティックシークレット": "PSER-OF",
    "グランドマスター": "GMR-OF",
}


def rarity_jp_to_en(rarity_jp: str) -> str:
    """将日文稀有度名转换为英文缩写。"""
    if not rarity_jp:
        return "未知"
    return RARITY_MAPPING.get(rarity_jp, rarity_jp)


def expand_rarity_to_jp_list(rarity_en: str) -> list[str]:
    """将英文稀有度缩写展开为所有匹配的日文名。"""
    upper = rarity_en.upper()
    return [
        jp
        for jp, en in RARITY_MAPPING.items()
        if en.upper().startswith(upper)
    ]


def clean_card_name(name: str) -> str:
    """删除 Cardrush 查询不需要的卡名标点和空白。"""
    if not name:
        return name
    name = name.replace("＜", "").replace("＞", "")
    return re.sub(
        r"[^\u4e00-\u9fff\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff"
        r"\u2200-\u22ffa-zA-Z0-9]",
        "",
        name,
    )


def parse_price_query(
    input_text: str,
) -> tuple[str, str | None, str | None]:
    """从输入末尾解析可选稀有度和卡盒编号。"""
    tokens = input_text.split()
    rarity_en: str | None = None
    model_prefix: str | None = None

    while len(tokens) > 1:
        last = tokens[-1]
        last_upper = last.upper()

        if rarity_en is None and any(
            en.upper().startswith(last_upper)
            for en in RARITY_MAPPING.values()
        ):
            rarity_en = last_upper
            tokens.pop()
            continue

        if (
            model_prefix is None
            and re.match(r"^[A-Z]{2,6}[0-9]{0,2}$", last)
        ):
            model_prefix = last
            tokens.pop()
            continue

        break

    return " ".join(tokens), rarity_en, model_prefix


async def resolve_card_name_jp(name: str) -> str:
    """尽可能把多语言卡名解析为 Cardrush 使用的日文名。"""
    card_info = await get_card_info(name)
    if card_info:
        return clean_card_name(card_info["jp_name"])
    return clean_card_name(name)
