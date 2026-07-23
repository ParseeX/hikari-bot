from io import BytesIO

import pytest
from PIL import Image, UnidentifiedImageError

from hikari_bot.features.cardrush.reporting.thumbnails import (
    write_card_thumbnail,
)


def source_png() -> bytes:
    image = Image.new("RGB", (800, 1200), "#204080")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_write_card_thumbnail_creates_display_sized_jpeg(tmp_path):
    destination = tmp_path / "card.jpg"

    write_card_thumbnail(source_png(), destination)

    with Image.open(destination) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (220, 264)


def test_write_card_thumbnail_rejects_invalid_data(tmp_path):
    with pytest.raises(UnidentifiedImageError):
        write_card_thumbnail(
            b"not-an-image",
            tmp_path / "card.jpg",
        )
