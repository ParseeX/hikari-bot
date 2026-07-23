from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..errors import CardrushRenderError

_MAX_QUALITY = 85
_MIN_QUALITY = 40
_QUALITY_STEP = 5
_RESIZE_FACTOR = 0.85
_MIN_WIDTH = 640


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )
    return buffer.getvalue()


def compress_for_qq(
    image_bytes: bytes,
    max_bytes: int = 1_000_000,
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            working = source.convert("RGB")
    except (OSError, UnidentifiedImageError) as error:
        raise CardrushRenderError(
            f"Unable to decode QQ report image: {error}"
        ) from error

    while True:
        for quality in range(
            _MAX_QUALITY,
            _MIN_QUALITY - 1,
            -_QUALITY_STEP,
        ):
            encoded = _encode_jpeg(working, quality)
            if len(encoded) <= max_bytes:
                return encoded

        if working.width <= _MIN_WIDTH:
            break
        next_width = max(
            _MIN_WIDTH,
            int(working.width * _RESIZE_FACTOR),
        )
        next_height = max(
            1,
            round(working.height * next_width / working.width),
        )
        working = working.resize(
            (next_width, next_height),
            Image.Resampling.LANCZOS,
        )

    raise CardrushRenderError(
        "Unable to compress QQ report image below "
        f"{max_bytes} bytes"
    )
