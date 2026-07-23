from io import BytesIO

import pytest
from PIL import Image, ImageDraw

import hikari_bot.features.cardrush.reporting.compression as compression
from hikari_bot.features.cardrush.errors import CardrushRenderError
from hikari_bot.features.cardrush.reporting.compression import (
    compress_for_qq,
)


def report_like_png() -> bytes:
    image = Image.new("RGB", (1080, 1920), "#081020")
    draw = ImageDraw.Draw(image)
    for row in range(7):
        for column in range(5):
            left = 24 + column * 206
            top = 140 + row * 244
            draw.rectangle(
                (left, top, left + 196, top + 232),
                fill=(20 + row * 8, 30 + column * 8, 55),
            )
            draw.rectangle(
                (left, top + 135, left + 196, top + 232),
                fill="#0a0f1d",
            )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compress_for_qq_hits_target_without_resizing():
    result = compress_for_qq(
        report_like_png(),
        target_bytes=350_000,
    )

    assert result.startswith(b"\xff\xd8")
    assert len(result) <= 350_000
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (1080, 1920)


def test_compress_for_qq_returns_quality_floor_without_resizing(
    monkeypatch,
):
    calls = []

    def fake_encode(image, quality, subsampling):
        calls.append((image.size, quality, subsampling))
        return bytes(500)

    monkeypatch.setattr(compression, "_encode_jpeg", fake_encode)

    result = compress_for_qq(
        report_like_png(),
        target_bytes=100,
    )

    assert len(result) == 500
    assert calls == [
        ((1080, 1920), quality, subsampling)
        for subsampling in (0, 1)
        for quality in (82, 80, 78, 76, 74, 72)
    ]


def test_compress_for_qq_uses_422_fallback_before_floor(
    monkeypatch,
):
    calls = []

    def fake_encode(image, quality, subsampling):
        calls.append((quality, subsampling))
        return bytes(500 if subsampling == 0 else 90)

    monkeypatch.setattr(compression, "_encode_jpeg", fake_encode)

    result = compress_for_qq(
        report_like_png(),
        target_bytes=100,
    )

    assert len(result) == 90
    assert calls == [
        (82, 0),
        (80, 0),
        (78, 0),
        (76, 0),
        (74, 0),
        (72, 0),
        (82, 1),
    ]


def test_compress_for_qq_rejects_invalid_image():
    with pytest.raises(CardrushRenderError, match="decode"):
        compress_for_qq(b"not-an-image")
