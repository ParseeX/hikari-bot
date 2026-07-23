from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 80
_MIN_QUALITY = 60
_QUALITY_STEP = 5
_TARGET_BYTES = 200_000


def _encode_webp(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="WEBP",
        quality=quality,
        method=6,
    )
    return buffer.getvalue()


def compress_for_qq(
    image_bytes: bytes,
    target_bytes: int = _TARGET_BYTES,
) -> bytes:
    if target_bytes <= 0:
        raise ValueError("target_bytes must be positive")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            working = source.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise CardrushRenderError(
            f"Unable to decode QQ report image: {error}"
        ) from error

    encoded = b""
    for quality in range(
        _MAX_QUALITY,
        _MIN_QUALITY - 1,
        -_QUALITY_STEP,
    ):
        encoded = _encode_webp(working, quality)
        if len(encoded) <= target_bytes:
            return encoded
    return encoded
