"""跨站版本标识；只转换经过核对的画面名称，不泛化删除括号内容。"""
from .models import normalized


# 已核对商品：原画 https://www.cardrush.jp/product/47814
# 异画 https://www.cardrush.jp/product/47815 。编号相同，必须同时核对画面名。
VERIFIED_EDITIONS = {
    (normalized('灰流うらら'), normalized('PAC1-JP016')):
        ('灰流うらら(立ち)', 'PAC1-JP016'),
    (normalized('灰流うらら'), normalized('PAC1-JP016(异画)')):
        ('[新]灰流うらら(座り)', 'PAC1-JP016'),
}


def cardrush_identity(name, version):
    """返回应核对的 Cardrush 完整卡名和编号；未知标记保持原样。"""
    return VERIFIED_EDITIONS.get((normalized(name), normalized(version.number)), (name, version.number))
