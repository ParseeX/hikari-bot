"""YGOPro 整理卡组的 deck_sort_lv 顺序，只改变展示，不参与卡组版本判定。"""
import sqlite3

from hikari_bot.core.config import settings
from hikari_bot.services.card_catalog import CardCatalog


# 同时核对 ReadDB：level 只取低 8 位，Link 的 defense 设为 0（原值是箭头）。
# https://github.com/Fluorohydride/ygopro/blob/bea3249a3124cd1e5873ae8a08182778956af8da/gframe/data_manager.cpp
def card_sort_key(code, data):
    if data is None or data[0] is None:
        return (1, code)
    card_type, level, attack, defense = data
    base_type = card_type & 0x7
    if base_type == 1:
        if level is None or attack is None or (defense is None and not card_type & 0x4000000):
            return (1, code)
        subtype = card_type & (0x48020c1 if card_type & 0x48020c0 else 0x31)
        # CDB 的攻击力、防御力按有符号 32 位数读取，负值代表未知数值。
        attack = (attack + 2**31) % 2**32 - 2**31
        defense = 0 if card_type & 0x4000000 else (defense + 2**31) % 2**32 - 2**31
        return (0, base_type, subtype, -(level & 0xff), -attack, -defense, code)
    return (0, base_type, card_type & 0xfffffff8, code)


def sort_sections(*sections, catalog=None):
    """各分区独立排序，重复卡保留；资料缺失时按卡号稳定排在末尾。"""
    sections = [tuple(section) for section in sections]
    codes = {code for section in sections for code in section}
    data = {}
    if codes:
        try:
            data = (catalog or CardCatalog(settings.card_catalog_path)).deck_sort_data(codes)
        except sqlite3.Error:
            # 卡片资料暂时不可用不能阻断录像归档或丢弃卡片。
            pass
    return tuple(tuple(sorted(section, key=lambda code: card_sort_key(code, data.get(code))))
                 for section in sections)
