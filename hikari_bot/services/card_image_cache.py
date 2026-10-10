"""完整卡面的校验与磁盘缓存；缺图不能被当作有效缓存。"""
import io
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageStat


# 中文源 textures/unknown.jpg 的 8×8 RGB 特征，可识别缩放或重新压缩后的占位图。
_UNKNOWN = bytes.fromhex(
    '706e74736e70736f716e6a6c716d6f7975777c797b6b6a707e7e83b7b7b9c4c4c6'
    'bdbdbebcbbbdbebec0b7b7b96c6b70797a80f7f8f9fffffffffffffffffffffffff6f6f7'
    '6e6d737e7f85f2f3f4fffffffffffffffffffffffff1f2f275757b7d7e84f7f9faffffff'
    'fffffffffffffffffff6f7f876767d716d72c6c5c6ceccccd0cfcfcac8c8cccbcbc4c3c3'
    '7471759c9491d7d5d3cecbcad2cfcececbcacecbcad1cecc968e8c867f80a49e9da8a3a2'
    'a39e9e9c9796a49f9e9c9695878081')


def valid_card_image(data):
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        with source.convert('RGBA') as rgba, Image.new('RGBA', source.size, 'white') as background:
            background.alpha_composite(rgba)
            with background.convert('RGB') as image:
                if max(ImageStat.Stat(image).stddev) < 1:
                    raise ValueError('空白卡图')
                with image.resize((8, 8)) as small:
                    signature = small.tobytes()
                if sum(abs(a - b) for a, b in zip(signature, _UNKNOWN)) / len(_UNKNOWN) < 3:
                    raise ValueError('未知卡占位图')
    return data


def read_card_image(path):
    try:
        return valid_card_image(Path(path).read_bytes())
    except (OSError, ValueError):
        return None


def write_card_image(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
