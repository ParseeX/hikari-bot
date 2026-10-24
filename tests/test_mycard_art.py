import io

from PIL import Image
import pytest

from hikari_bot.services.mycard_archive.art import square_card_art


@pytest.mark.parametrize('size', [(900, 300), (300, 900), (300, 300)])
def test_square_art_crops_center_without_padding_or_stretching(size):
    image = Image.new('RGB', size, 'blue')
    left, top = (size[0] - 300) // 2, (size[1] - 300) // 2
    image.paste(Image.new('RGB', (300, 300), 'red'), (left, top))
    source = io.BytesIO(); image.save(source, format='PNG')
    with Image.open(io.BytesIO(square_card_art(source.getvalue()))) as result:
        assert result.size == (624, 624)
        for point in ((0, 0), (623, 0), (0, 623), (623, 623), (312, 312)):
            red, green, blue = result.getpixel(point)
            # 裁切边缘会有 Lanczos/JPEG 的轻微色彩混合，不能把它误判为补白。
            assert red > 200 and green < 15 and blue < 65
