from io import BytesIO

import pytest
from PIL import Image

from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.reporting.compression import (
    compress_for_qq,
)


def noisy_png() -> bytes:
    image = Image.effect_noise((1800, 1200), 100).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compress_for_qq_returns_jpeg_within_limit_and_keeps_ratio():
    source = noisy_png()

    result = compress_for_qq(source, max_bytes=200_000)

    assert len(result) <= 200_000
    assert result.startswith(b"\xff\xd8")
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.width / image.height == pytest.approx(
            1800 / 1200,
            rel=0.01,
        )


def test_compress_for_qq_rejects_invalid_image():
    with pytest.raises(CardrushRenderError, match="decode"):
        compress_for_qq(b"not-an-image")
