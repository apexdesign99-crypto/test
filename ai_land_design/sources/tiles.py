"""地図タイルの座標計算と PNG デコード（標準ライブラリのみ）。

ハザードマップポータル等が配信するラスタタイルから、指定した緯度経度の
画素値を取り出すために使う。Pillow を持ち込まずに済むよう、8bit の
PNG（パレット / RGB / RGBA・グレースケール）だけを自前でデコードする。
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import List, Tuple

RGBA = Tuple[int, int, int, int]


@dataclass
class TileCoord:
    """タイル座標と、そのタイル内の画素位置。"""

    z: int
    x: int
    y: int
    px: int
    py: int


def lonlat_to_tile(lat: float, lon: float, zoom: int, tile_size: int = 256) -> TileCoord:
    """緯度経度 → Web メルカトルのタイル座標・画素位置。"""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    tile_x, tile_y = int(x), int(y)
    return TileCoord(
        z=zoom,
        x=tile_x,
        y=tile_y,
        px=min(tile_size - 1, int((x - tile_x) * tile_size)),
        py=min(tile_size - 1, int((y - tile_y) * tile_size)),
    )


class PngDecodeError(ValueError):
    """PNG を解釈できなかった。"""


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode_png(data: bytes) -> Tuple[int, int, List[List[RGBA]]]:
    """8bit PNG をデコードして (幅, 高さ, RGBA の 2 次元配列) を返す。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngDecodeError("PNG シグネチャではない")

    offset = 8
    width = height = bit_depth = color_type = 0
    idat = bytearray()
    palette: List[Tuple[int, int, int]] = []
    transparency: List[int] = []

    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        offset += 12 + length  # 長さ + 型 + データ + CRC

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if bit_depth != 8:
                raise PngDecodeError(f"未対応のビット深度: {bit_depth}")
            if interlace:
                raise PngDecodeError("インターレース PNG は未対応")
        elif chunk_type == b"PLTE":
            palette = [tuple(chunk[i : i + 3]) for i in range(0, len(chunk), 3)]
        elif chunk_type == b"tRNS":
            transparency = list(chunk)
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PngDecodeError(f"未対応のカラータイプ: {color_type}")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    pixels: List[List[RGBA]] = []
    previous = bytearray(stride)
    position = 0

    for _ in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride

        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            up_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif filter_type == 4:
                line[i] = (line[i] + _paeth(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                raise PngDecodeError(f"未知のフィルタ: {filter_type}")

        row: List[RGBA] = []
        for x in range(width):
            chunk = line[x * channels : (x + 1) * channels]
            if color_type == 0:
                row.append((chunk[0], chunk[0], chunk[0], 255))
            elif color_type == 2:
                row.append((chunk[0], chunk[1], chunk[2], 255))
            elif color_type == 3:
                index = chunk[0]
                r, g, b = palette[index] if index < len(palette) else (0, 0, 0)
                alpha = transparency[index] if index < len(transparency) else 255
                row.append((r, g, b, alpha))
            elif color_type == 4:
                row.append((chunk[0], chunk[0], chunk[0], chunk[1]))
            else:
                row.append((chunk[0], chunk[1], chunk[2], chunk[3]))
        pixels.append(row)
        previous = line

    return width, height, pixels


def pixel_for(data: bytes, coord: TileCoord, tile_size: int = 256) -> RGBA:
    """タイル座標が指す画素の RGBA。

    `TileCoord` の画素位置は 256px のタイルを前提に計算されるため、
    実際の画像サイズ（512px の高解像度タイルなど）に合わせて拡大縮小する。
    """
    width, height, pixels = decode_png(data)
    x = min(width - 1, coord.px * width // tile_size)
    y = min(height - 1, coord.py * height // tile_size)
    return pixels[y][x]


def pixel_at(data: bytes, px: int, py: int) -> RGBA:
    """PNG の指定画素の RGBA。"""
    width, height, pixels = decode_png(data)
    if not (0 <= px < width and 0 <= py < height):
        raise PngDecodeError(f"画素が範囲外: ({px}, {py}) / {width}x{height}")
    return pixels[py][px]
