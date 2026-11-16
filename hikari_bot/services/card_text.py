"""Platform-independent card text. No configuration, database or bot imports."""


def format_card_effect(info: dict) -> str:
    """Preserve the original OneBot effect layout, without transport limits."""
    official_name = info.get("jp_name") or info.get("en_name", "") or ""
    cn_name = info.get("cn_name", "") or ""
    texts = info.get("text") or {}
    header = f"{cn_name}（{official_name}）" if cn_name else official_name
    result = f"{header}\n{texts.get('types') or ''}\n"
    pendulum = texts.get("pdesc") or ""
    if pendulum:
        result += pendulum + "\n---------------\n"
    return result + (texts.get("desc") or "暂无效果资料。")
