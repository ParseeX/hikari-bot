from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 82
_MIN_QUALITY = 72
_QUALITY_STEP = 2
_TARGET_BYTES = 350_000


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=0,
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
        encoded = _encode_jpeg(working, quality)
        if len(encoded) <= target_bytes:
            return encoded
    return encoded
