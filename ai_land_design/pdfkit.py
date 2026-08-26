"""PDF 出力（標準ライブラリのみ）。

申請図書をそのまま渡せる PDF を作るための最小実装。外部ライブラリを使わず、
次を自前で行う。

* TrueType フォントの解析（cmap / hmtx / loca / glyf）と**サブセット化**
  — 日本語フォントは 6MB 前後あるため、使った字だけを取り出して埋め込む
* 合成フォント（Type0 / CIDFontType2 / Identity-H）としての埋め込み
  — 日本語をそのまま描画でき、ToUnicode CMap によりコピー・検索もできる
* ベクタ描画（線・多角形・矩形）とテキスト配置

座標系は PDF と同じ「左下原点・y 上向き」で、図面のワールド座標と一致する。
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: 用紙サイズ [pt]
A4_PORTRAIT = (595.28, 841.89)
A4_LANDSCAPE = (841.89, 595.28)
A3_LANDSCAPE = (1190.55, 841.89)

#: 日本語フォントの探索先（先に見つかったものを使う）
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/etc/alternatives/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
)


class FontError(RuntimeError):
    """フォントを読み込めない。"""


def find_japanese_font(candidates: Sequence[str] = FONT_CANDIDATES) -> Optional[Path]:
    """日本語フォント（TrueType）を探す。"""
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and path.suffix.lower() in (".ttf", ".otf", ".ttc"):
            return path
    return None


class TrueTypeFont:
    """TrueType フォントの読み込みとサブセット化。

    PDF に CIDFontType2 として埋め込むために必要な情報だけを扱う。
    OpenType/CFF（グリフが glyf ではなく CFF に入る形式）には対応しない。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FontError(f"フォントファイルがありません: {self.path}")
        self.data = self.path.read_bytes()
        if self.data[:4] == b"ttcf":  # TrueType Collection は先頭のフォントを使う
            offset = struct.unpack(">I", self.data[12:16])[0]
        else:
            offset = 0
        if self.data[offset : offset + 4] not in (b"\x00\x01\x00\x00", b"true"):
            raise FontError(f"TrueType 形式ではありません: {self.path}")

        table_count = struct.unpack(">H", self.data[offset + 4 : offset + 6])[0]
        self.tables: Dict[str, Tuple[int, int]] = {}
        for index in range(table_count):
            entry = offset + 12 + index * 16
            tag = self.data[entry : entry + 4].decode("latin-1")
            table_offset, length = struct.unpack(">II", self.data[entry + 8 : entry + 16])
            self.tables[tag] = (table_offset, length)
        for required in ("head", "hhea", "hmtx", "maxp", "loca", "glyf", "cmap"):
            if required not in self.tables:
                raise FontError(f"必要なテーブルがありません: {required}")

        head_offset = self.tables["head"][0]
        self.units_per_em = struct.unpack(">H", self.data[head_offset + 18 : head_offset + 20])[0]
        self.index_to_loc_format = struct.unpack(
            ">h", self.data[head_offset + 50 : head_offset + 52]
        )[0]
        self.bbox = struct.unpack(">hhhh", self.data[head_offset + 36 : head_offset + 44])

        hhea_offset = self.tables["hhea"][0]
        self.ascent, self.descent = struct.unpack(
            ">hh", self.data[hhea_offset + 4 : hhea_offset + 8]
        )
        self.number_of_hmetrics = struct.unpack(
            ">H", self.data[hhea_offset + 34 : hhea_offset + 36]
        )[0]

        maxp_offset = self.tables["maxp"][0]
        self.num_glyphs = struct.unpack(">H", self.data[maxp_offset + 4 : maxp_offset + 6])[0]

        self._loca = self._read_loca()
        self._cmap = self._read_cmap()
        self.name = self.path.stem.replace(" ", "")

    # ---- テーブルの読み取り ----
    def _read_loca(self) -> List[int]:
        offset, _ = self.tables["loca"]
        count = self.num_glyphs + 1
        if self.index_to_loc_format == 0:
            values = struct.unpack(f">{count}H", self.data[offset : offset + count * 2])
            return [v * 2 for v in values]
        return list(struct.unpack(f">{count}I", self.data[offset : offset + count * 4]))

    def _read_cmap(self) -> Dict[int, int]:
        offset, _ = self.tables["cmap"]
        table_count = struct.unpack(">H", self.data[offset + 2 : offset + 4])[0]
        best: Optional[Tuple[int, int]] = None  # (優先度, サブテーブル位置)
        for index in range(table_count):
            entry = offset + 4 + index * 8
            platform, encoding, sub_offset = struct.unpack(">HHI", self.data[entry : entry + 8])
            table_offset = offset + sub_offset
            fmt = struct.unpack(">H", self.data[table_offset : table_offset + 2])[0]
            priority = {(3, 10): 3, (3, 1): 2, (0, 3): 1}.get((platform, encoding), 0)
            if fmt in (4, 12) and (best is None or priority > best[0]):
                best = (priority, table_offset)
        if best is None:
            raise FontError("対応する cmap サブテーブルがありません")

        table_offset = best[1]
        fmt = struct.unpack(">H", self.data[table_offset : table_offset + 2])[0]
        mapping: Dict[int, int] = {}
        if fmt == 4:
            seg_x2 = struct.unpack(">H", self.data[table_offset + 6 : table_offset + 8])[0]
            segments = seg_x2 // 2
            base = table_offset + 14
            ends = struct.unpack(f">{segments}H", self.data[base : base + seg_x2])
            base += seg_x2 + 2
            starts = struct.unpack(f">{segments}H", self.data[base : base + seg_x2])
            base += seg_x2
            deltas = struct.unpack(f">{segments}h", self.data[base : base + seg_x2])
            range_offset_base = base + seg_x2
            offsets = struct.unpack(
                f">{segments}H", self.data[range_offset_base : range_offset_base + seg_x2]
            )
            for i in range(segments):
                for code in range(starts[i], min(ends[i], 0xFFFF) + 1):
                    if offsets[i] == 0:
                        glyph = (code + deltas[i]) & 0xFFFF
                    else:
                        position = (
                            range_offset_base + i * 2 + offsets[i] + (code - starts[i]) * 2
                        )
                        glyph = struct.unpack(">H", self.data[position : position + 2])[0]
                        if glyph:
                            glyph = (glyph + deltas[i]) & 0xFFFF
                    if glyph:
                        mapping[code] = glyph
        else:  # format 12
            group_count = struct.unpack(">I", self.data[table_offset + 12 : table_offset + 16])[0]
            for index in range(group_count):
                entry = table_offset + 16 + index * 12
                start, end, glyph = struct.unpack(">III", self.data[entry : entry + 12])
                for code in range(start, min(end, 0x10FFFF) + 1):
                    mapping[code] = glyph + (code - start)
        return mapping

    # ---- 参照 ----
    def gid(self, char: str) -> int:
        return self._cmap.get(ord(char), 0)

    def advance(self, gid: int) -> int:
        """グリフの送り幅（フォント単位）。"""
        offset, _ = self.tables["hmtx"]
        index = min(gid, self.number_of_hmetrics - 1)
        position = offset + index * 4
        return struct.unpack(">H", self.data[position : position + 2])[0]

    def width_1000(self, gid: int) -> float:
        """1000 単位系での送り幅（PDF の単位）。"""
        return self.advance(gid) * 1000.0 / self.units_per_em

    def text_width(self, text: str, size: float) -> float:
        """描画したときの文字列幅 [pt]。"""
        return sum(self.width_1000(self.gid(ch)) for ch in text) * size / 1000.0

    def _glyph_data(self, gid: int) -> bytes:
        offset, _ = self.tables["glyf"]
        start, end = self._loca[gid], self._loca[gid + 1]
        return self.data[offset + start : offset + end]

    def _components(self, gid: int) -> List[int]:
        """合成グリフが参照する構成グリフ。"""
        data = self._glyph_data(gid)
        if len(data) < 10 or struct.unpack(">h", data[:2])[0] >= 0:
            return []
        components: List[int] = []
        position = 10
        while position + 4 <= len(data):
            flags, glyph_index = struct.unpack(">HH", data[position : position + 4])
            components.append(glyph_index)
            position += 4
            position += 4 if flags & 0x0001 else 2  # ARG_1_AND_2_ARE_WORDS
            if flags & 0x0008:  # WE_HAVE_A_SCALE
                position += 2
            elif flags & 0x0040:  # X_AND_Y_SCALE
                position += 4
            elif flags & 0x0080:  # TWO_BY_TWO
                position += 8
            if not flags & 0x0020:  # MORE_COMPONENTS
                break
        return components

    def expand(self, gids: Iterable[int]) -> List[int]:
        """合成グリフの構成要素まで含めた GID の一覧。"""
        needed = {0}
        queue = list(gids)
        while queue:
            gid = queue.pop()
            if gid in needed or gid >= self.num_glyphs:
                continue
            needed.add(gid)
            queue.extend(self._components(gid))
        return sorted(needed)

    def subset(self, gids: Iterable[int]) -> bytes:
        """使用グリフだけを残した TrueType を組み立てる。

        GID の番号は変えない（CIDToGIDMap を Identity のままにできる）。
        未使用グリフは長さ 0 にするため、loca は残るがデータは含まれない。
        """
        used = set(self.expand(gids))
        glyf = bytearray()
        loca: List[int] = []
        for gid in range(self.num_glyphs):
            loca.append(len(glyf))
            if gid in used:
                data = self._glyph_data(gid)
                glyf += data
                if len(glyf) % 4:  # 4 バイト境界に揃える
                    glyf += b"\x00" * (4 - len(glyf) % 4)
        loca.append(len(glyf))

        head = bytearray(self.data[self.tables["head"][0] : self.tables["head"][0] + self.tables["head"][1]])
        struct.pack_into(">I", head, 8, 0)  # checkSumAdjustment
        struct.pack_into(">h", head, 50, 1)  # indexToLocFormat = long

        tables = {
            "head": bytes(head),
            "hhea": self._table_bytes("hhea"),
            "maxp": self._table_bytes("maxp"),
            "hmtx": self._table_bytes("hmtx"),
            "loca": struct.pack(f">{len(loca)}I", *loca),
            "glyf": bytes(glyf),
        }
        return _build_sfnt(tables)

    def _table_bytes(self, tag: str) -> bytes:
        offset, length = self.tables[tag]
        return self.data[offset : offset + length]


def _checksum(data: bytes) -> int:
    padded = data + b"\x00" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF


def _build_sfnt(tables: Dict[str, bytes]) -> bytes:
    """テーブル群から TrueType ファイルを組み立てる。"""
    tags = sorted(tables)
    count = len(tags)
    search_range = 16
    entry_selector = 0
    while search_range * 2 <= count * 16:
        search_range *= 2
        entry_selector += 1

    header = struct.pack(
        ">IHHHH", 0x00010000, count, search_range, entry_selector, count * 16 - search_range
    )
    directory = bytearray()
    body = bytearray()
    offset = 12 + count * 16
    for tag in tags:
        data = tables[tag]
        padded = data + b"\x00" * (-len(data) % 4)
        directory += struct.pack(
            ">4sIII", tag.encode("latin-1"), _checksum(data), offset + len(body), len(data)
        )
        body += padded
    return bytes(header + directory + body)


# --------------------------------------------------------------------------
# PDF の組み立て
# --------------------------------------------------------------------------


def _pdf_text(text: str) -> str:
    """PDF の文字列リテラル。

    非 ASCII を含む場合は UTF-16BE（BOM 付き）の16進表記にする。
    リテラル形式は latin-1 しか表現できないため。
    """
    if all(ord(ch) < 128 for ch in text):
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        return f"({escaped})"
    data = "FEFF" + "".join(f"{b:02X}" for b in text.encode("utf-16-be"))
    return f"<{data}>"


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


@dataclass
class Page:
    """1 ページ分の内容。座標は左下原点・y 上向き [pt]。"""

    width: float
    height: float
    document: "PdfDocument"
    operations: List[str] = field(default_factory=list)

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.6,
             color: Tuple[float, float, float] = (0, 0, 0), dash: Optional[Sequence[float]] = None) -> None:
        self._stroke_style(width, color, dash)
        self.operations.append(f"{_number(x1)} {_number(y1)} m {_number(x2)} {_number(y2)} l S")

    def polygon(self, points: Sequence[Tuple[float, float]], stroke_width: float = 0.6,
                stroke: Optional[Tuple[float, float, float]] = (0, 0, 0),
                fill: Optional[Tuple[float, float, float]] = None,
                close: bool = True, dash: Optional[Sequence[float]] = None) -> None:
        if len(points) < 2:
            return
        self._stroke_style(stroke_width, stroke or (0, 0, 0), dash)
        if fill:
            self.operations.append(f"{_number(fill[0])} {_number(fill[1])} {_number(fill[2])} rg")
        path = [f"{_number(points[0][0])} {_number(points[0][1])} m"]
        path += [f"{_number(x)} {_number(y)} l" for x, y in points[1:]]
        if close:
            path.append("h")
        painter = "B" if (fill and stroke) else ("f" if fill else "S")
        self.operations.append(" ".join(path) + f" {painter}")

    def rect(self, x: float, y: float, width: float, height: float, **kwargs) -> None:
        self.polygon(
            [(x, y), (x + width, y), (x + width, y + height), (x, y + height)], **kwargs
        )

    def text(self, x: float, y: float, content: str, size: float = 10.0,
             color: Tuple[float, float, float] = (0, 0, 0), align: str = "left",
             rotate: float = 0.0) -> None:
        """文字列を描画する（align: left / center / right、rotate: 反時計回りの角度）。"""
        if not content:
            return
        font = self.document.font
        width = font.text_width(content, size)
        offset = {"center": -width / 2, "right": -width}.get(align, 0.0)
        self.document.use(content)
        hex_string = "".join(f"{font.gid(ch):04X}" for ch in content)
        color_op = f"{_number(color[0])} {_number(color[1])} {_number(color[2])} rg"

        if rotate:
            radians = math.radians(rotate)
            cos, sin = math.cos(radians), math.sin(radians)
            # 回転後の向きに沿って揃え位置をずらす
            start_x = x + offset * cos
            start_y = y + offset * sin
            matrix = (
                f"{_number(cos)} {_number(sin)} {_number(-sin)} {_number(cos)} "
                f"{_number(start_x)} {_number(start_y)}"
            )
            self.operations.append(
                f"BT {color_op} /F1 {_number(size)} Tf {matrix} Tm <{hex_string}> Tj ET"
            )
            return

        self.operations.append(
            f"BT {color_op} /F1 {_number(size)} Tf {_number(x + offset)} {_number(y)} Td "
            f"<{hex_string}> Tj ET"
        )

    def _stroke_style(self, width: float, color: Tuple[float, float, float],
                      dash: Optional[Sequence[float]]) -> None:
        self.operations.append(f"{_number(width)} w")
        self.operations.append(f"{_number(color[0])} {_number(color[1])} {_number(color[2])} RG")
        if dash:
            pattern = " ".join(_number(v) for v in dash)
            self.operations.append(f"[{pattern}] 0 d")
        else:
            self.operations.append("[] 0 d")

    def content(self) -> bytes:
        return "\n".join(self.operations).encode("latin-1", errors="replace")


class PdfDocument:
    """複数ページの PDF を組み立てる。"""

    def __init__(self, font_path: Optional[str | Path] = None, title: str = ""):
        path = Path(font_path) if font_path else find_japanese_font()
        if path is None:
            raise FontError(
                "日本語フォントが見つかりません。font_path でフォントファイルを指定してください。"
            )
        self.font = TrueTypeFont(path)
        self.title = title
        self.pages: List[Page] = []
        self._used_gids: Dict[int, str] = {0: ""}

    def use(self, text: str) -> None:
        """埋め込み対象のグリフとして記録する。"""
        for char in text:
            self._used_gids.setdefault(self.font.gid(char), char)

    def add_page(self, size: Tuple[float, float] = A4_PORTRAIT) -> Page:
        page = Page(width=size[0], height=size[1], document=self)
        self.pages.append(page)
        return page

    # ---- 出力 ----
    def _font_objects(self, next_id: int) -> Tuple[List[bytes], int, int]:
        """フォント関連のオブジェクトを組み立てる。戻り値は (objects, Type0 の id, 次の id)。"""
        gids = sorted(self._used_gids)
        subset = self.font.subset(gids)
        compressed = zlib.compress(subset)

        file_id = next_id
        descriptor_id = next_id + 1
        cid_font_id = next_id + 2
        to_unicode_id = next_id + 3
        type0_id = next_id + 4

        scale = 1000.0 / self.font.units_per_em
        widths = " ".join(
            f"{gid} [{_number(self.font.width_1000(gid))}]" for gid in gids if gid
        )
        bbox = " ".join(_number(v * scale) for v in self.font.bbox)

        # ToUnicode CMap（コピー・検索を可能にする）
        entries = [
            (gid, char) for gid, char in sorted(self._used_gids.items()) if gid and char
        ]
        def utf16be_hex(text: str) -> str:
            data = text.encode("utf-16-be")
            return "".join(f"{value:02X}" for value in data)

        bf_chars = "".join(
            f"<{gid:04X}> <{utf16be_hex(char)}>\n" for gid, char in entries
        )
        cmap = (
            "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "/CIDSystemInfo <</Registry (Adobe) /Ordering (UCS) /Supplement 0>> def\n"
            "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
            f"{len(entries)} beginbfchar\n{bf_chars}endbfchar\n"
            "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend"
        )
        cmap_data = zlib.compress(cmap.encode("utf-8"))

        objects = [
            _stream_object(
                file_id,
                f"/Length1 {len(subset)} /Filter /FlateDecode",
                compressed,
            ),
            _dict_object(
                descriptor_id,
                f"/Type /FontDescriptor /FontName /{self.font.name} /Flags 4 "
                f"/FontBBox [{bbox}] /ItalicAngle 0 "
                f"/Ascent {_number(self.font.ascent * scale)} "
                f"/Descent {_number(self.font.descent * scale)} "
                f"/CapHeight {_number(self.font.ascent * scale)} /StemV 80 "
                f"/FontFile2 {file_id} 0 R",
            ),
            _dict_object(
                cid_font_id,
                f"/Type /Font /Subtype /CIDFontType2 /BaseFont /{self.font.name} "
                "/CIDSystemInfo <</Registry (Adobe) /Ordering (Identity) /Supplement 0>> "
                f"/FontDescriptor {descriptor_id} 0 R /DW 1000 /W [{widths}] "
                "/CIDToGIDMap /Identity",
            ),
            _stream_object(to_unicode_id, "/Filter /FlateDecode", cmap_data),
            _dict_object(
                type0_id,
                f"/Type /Font /Subtype /Type0 /BaseFont /{self.font.name} "
                f"/Encoding /Identity-H /DescendantFonts [{cid_font_id} 0 R] "
                f"/ToUnicode {to_unicode_id} 0 R",
            ),
        ]
        return objects, type0_id, type0_id + 1

    def output(self) -> bytes:
        """PDF のバイト列を返す。"""
        if not self.pages:
            self.add_page()

        catalog_id, pages_id = 1, 2
        next_id = 3
        page_ids: List[int] = []
        page_objects: List[bytes] = []
        content_objects: List[bytes] = []
        for page in self.pages:
            page_ids.append(next_id)
            next_id += 1
        content_ids = []
        for _ in self.pages:
            content_ids.append(next_id)
            next_id += 1

        font_objects, font_id, next_id = self._font_objects(next_id)

        for page, page_id, content_id in zip(self.pages, page_ids, content_ids):
            page_objects.append(
                _dict_object(
                    page_id,
                    f"/Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {_number(page.width)} {_number(page.height)}] "
                    f"/Resources <</Font <</F1 {font_id} 0 R>>>> "
                    f"/Contents {content_id} 0 R",
                )
            )
            content_objects.append(
                _stream_object(content_id, "/Filter /FlateDecode", zlib.compress(page.content()))
            )

        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects = [
            _dict_object(catalog_id, f"/Type /Catalog /Pages {pages_id} 0 R"),
            _dict_object(pages_id, f"/Type /Pages /Kids [{kids}] /Count {len(page_ids)}"),
            *page_objects,
            *content_objects,
            *font_objects,
        ]

        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets: Dict[int, int] = {}
        for obj in objects:
            number = int(obj.split(b" ", 1)[0])
            offsets[number] = len(out)
            out += obj
        total = max(offsets) + 1

        xref_position = len(out)
        out += f"xref\n0 {total}\n".encode("latin-1")
        out += b"0000000000 65535 f \n"
        for number in range(1, total):
            out += f"{offsets.get(number, 0):010d} 00000 n \n".encode("latin-1")
        info = (
            f"/Title {_pdf_text(self.title)} /Producer (ai-land-design)" if self.title else ""
        )
        out += (
            f"trailer\n<</Size {total} /Root {catalog_id} 0 R"
            + (f" /Info <<{info}>>" if info else "")
            + f">>\nstartxref\n{xref_position}\n%%EOF\n"
        ).encode("latin-1")
        return bytes(out)


def _dict_object(number: int, body: str) -> bytes:
    return f"{number} 0 obj\n<<{body}>>\nendobj\n".encode("latin-1")


def _stream_object(number: int, options: str, data: bytes) -> bytes:
    header = f"{number} 0 obj\n<</Length {len(data)} {options}>>\nstream\n".encode("latin-1")
    return header + data + b"\nendstream\nendobj\n"
