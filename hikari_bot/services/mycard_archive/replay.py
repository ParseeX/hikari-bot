"""按 MyCard 官方 YRP1/YRP2 格式提取主卡组与额外卡组；Side 始终未知。"""
import lzma
import struct
from dataclasses import dataclass

from .sorting import sort_sections

MAX_DATA = 0x80000


@dataclass(frozen=True)
class Deck:
    player: str
    main: tuple[int, ...]
    extra: tuple[int, ...]
    side: tuple[int, ...] = ()

    def ydk(self):
        main, extra, side = sort_sections(self.main, self.extra, self.side)
        return '\n'.join([
            '#created by hikari-bot; Side INCOMPLETE (observed substitutions only)',
            '#main', *map(str, main), '#extra', *map(str, extra),
            '!side', *map(str, side),
            '# Side INCOMPLETE; unobserved side cards remain unknown', '',
        ])


def parse_replay(data: bytes) -> list[Deck]:
    if not 32 <= len(data) <= MAX_DATA + 80:
        raise ValueError('invalid replay size')
    magic, version, flags, _, declared, _ = struct.unpack_from('<6I', data)
    if magic not in (0x31707279, 0x32707279):
        raise ValueError('unsupported replay magic')
    if version < 0x12D0 or flags & ~0x1F or flags & 8:
        raise ValueError('unsupported replay version or mode')
    if version >= 0x1353 and not flags & 0x10:
        raise ValueError('non-uniform modern replay')
    header_size = 80 if magic == 0x32707279 else 32
    if len(data) < header_size:
        raise ValueError('truncated extended header')
    payload = data[header_size:]
    if flags & 1:
        if not 0 < declared <= MAX_DATA:
            raise ValueError('invalid decompressed size')
        stream = data[24:29] + struct.pack('<Q', declared) + payload
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=64 * 1024 * 1024)
        try:
            payload = decoder.decompress(stream, max_length=MAX_DATA + 1)
        except lzma.LZMAError as exc:
            raise ValueError('invalid replay compression') from exc
        if len(payload) != declared or not decoder.eof:
            raise ValueError('truncated or oversized replay payload')
    elif len(payload) > MAX_DATA:
        raise ValueError('oversized replay payload')
    pos = 0

    def take(size):
        nonlocal pos
        if size < 0 or pos + size > len(payload):
            raise ValueError('truncated replay metadata')
        value = payload[pos:pos+size]
        pos += size
        return value

    count = 4 if flags & 2 else 2
    players = [take(40).decode('utf-16-le').split('\0', 1)[0] for _ in range(count)]
    take(16)  # 初始 LP、手牌数、抽卡数、规则标志。

    def cards():
        count = struct.unpack('<I', take(4))[0]
        if count > 100:
            raise ValueError('invalid deck count')
        return struct.unpack('<' + 'I' * count, take(4 * count))

    return [Deck(player, cards(), cards()) for player in players]
