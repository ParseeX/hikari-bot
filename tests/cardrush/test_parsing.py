from hikari_bot.features.cardrush.parsing import (
    clean_card_name,
    expand_rarity_to_jp_list,
    parse_price_query,
    rarity_jp_to_en,
)


def test_parse_price_query_preserves_existing_filters():
    assert parse_price_query("青眼白龙 ALIN UR") == ("青眼白龙", "UR", "ALIN")
    assert parse_price_query("Blue-Eyes UR ALIN") == ("Blue-Eyes", "UR", "ALIN")
    assert parse_price_query("青眼白龙") == ("青眼白龙", None, None)


def test_rarity_prefix_expands_all_matching_japanese_names():
    values = expand_rarity_to_jp_list("PSER")
    assert "プリズマティックシークレット" in values
    assert "OFプリズマティックシークレット" in values


def test_name_cleanup_and_rarity_display_match_current_behavior():
    assert clean_card_name("＜青眼・白龍＞") == "青眼白龍"
    assert rarity_jp_to_en("ウルトラ") == "UR"
    assert rarity_jp_to_en("未知レア") == "未知レア"
