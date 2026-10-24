"""JPEG deck previews for chat; the website and disk PNG exports stay lossless."""
import base64
import io

from PIL import Image

from hikari_bot.services.deck_png import _run


def _encode(image, as_base64=False):
    # JPEG has no alpha channel. Deck canvases are RGB; flatten other inputs
    # against the same white background instead of turning transparency black.
    if image.mode == "RGB":
        source = image
    elif image.mode in ("RGBA", "LA") or "transparency" in image.info:
        source = Image.new("RGB", image.size, "white")
        with image.convert("RGBA") as rgba:
            source.paste(rgba, mask=rgba.getchannel("A"))
    else:
        source = image.convert("RGB")
    candidate = source
    try:
        # 先取原尺寸的 70%，再缩小 10%；只重采样一次，不按字节大小反复缩图。
        size = tuple(max(1, value * 7 // 10 * 9 // 10) for value in source.size)
        candidate = source.resize(size, Image.Resampling.LANCZOS)
        with io.BytesIO() as buffer:
            candidate.save(buffer, format="JPEG", quality=70,
                           subsampling=2, optimize=True, progressive=False)
            data = buffer.getvalue()
        return base64.b64encode(data).decode("ascii") if as_base64 else data
    finally:
        if candidate is not source:
            candidate.close()
        if source is not image:
            source.close()


async def encode_deck_message_image(image) -> bytes:
    """返回约 63% 尺寸、Q70 的 JPEG，不限制字节数；接管图片生命周期。"""
    return await _run(image, _encode)


async def encode_deck_message_base64(image) -> str:
    """Same fixed JPEG before base64 transport, using the shared two-worker pool."""
    return await _run(image, _encode, True)
