from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

_THUMBNAIL_SIZE = (220, 264)


def write_card_thumbnail(
    image_bytes: bytes,
    destination: Path,
) -> None:
    with Image.open(BytesIO(image_bytes)) as source:
        source.load()
        thumbnail = ImageOps.fit(
            source.convert("RGB"),
            _THUMBNAIL_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.25),
        )
    thumbnail.save(
        destination,
        format="JPEG",
        quality=64,
        optimize=True,
        progressive=True,
        subsampling=2,
    )
