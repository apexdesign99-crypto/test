"""作図キット。SVG と PDF の両方に出力できる。

建築図面に必要な要素（線・矩形・寸法線・方位記号・表）だけを備える。
描画命令はいったんキャンバス座標（左上原点・y 下向き・px）の表示リストとして
持ち、最後に SVG か PDF に変換する。図面のコードを 1 つに保つための構造。

ワールド座標は「x 右、y 上」の実寸 [m]。SVG は y が下向きのため Canvas 側で反転する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple, Union

Point = Tuple[float, float]

THIN = 0.8
MEDIUM = 1.6
THICK = 2.6
INK = "#1d1b17"
GRAY = "#8a8577"
ACCENT = "#2f6f4f"


def rgb(color: str) -> Tuple[float, float, float]:
    """#rrggbb → (0-1, 0-1, 0-1)。"""
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


@dataclass
class Line:
    a: Point
    b: Point
    width: float
    color: str
    dash: str = ""
    title: str = ""


@dataclass
class Polygon:
    points: List[Point]
    fill: str
    stroke: str
    width: float
    dash: str = ""
    close: bool = True


@dataclass
class Text:
    point: Point
    content: str
    size: float
    anchor: str  # start / middle / end
    color: str
    weight: str = "normal"
    rotate: float = 0.0
    title: str = ""  # SVG のツールチップ（PDF では出力しない）


Primitive = Union[Line, Polygon, Text]


@dataclass
class Canvas:
    """ワールド座標 [m] を描くためのキャンバス。

    描画命令はキャンバス座標（px）の表示リストとして保持し、
    `render()` で SVG に、`draw_on()` で PDF ページに出力する。
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    scale: float = 24.0  # 1m あたりの px
    margin_m: float = 2.0
    title: str = ""
    subtitle: str = ""
    items: List[Primitive] = field(default_factory=list)

    @property
    def width_px(self) -> float:
        return (self.max_x - self.min_x + self.margin_m * 2) * self.scale

    @property
    def height_px(self) -> float:
        return (self.max_y - self.min_y + self.margin_m * 2) * self.scale + 34

    def px(self, point: Point) -> Point:
        """ワールド座標 → キャンバス座標（px, 左上原点）。"""
        x, y = point
        return (
            (x - self.min_x + self.margin_m) * self.scale,
            (self.max_y - y + self.margin_m) * self.scale + 34,
        )

    # ---- 基本図形（ワールド座標） ----
    def line(self, a: Point, b: Point, width: float = THIN, color: str = INK, dash: str = "",
             title: str = "") -> None:
        self.items.append(Line(self.px(a), self.px(b), width, color, dash, title))

    def polyline(self, points: Sequence[Point], width: float = THIN, color: str = INK,
                 dash: str = "", fill: str = "none") -> None:
        self.items.append(
            Polygon([self.px(p) for p in points], fill, color, width, dash, close=False)
        )

    def polygon(self, points: Sequence[Point], fill: str = "none", stroke: str = INK,
                width: float = THIN, dash: str = "") -> None:
        self.items.append(Polygon([self.px(p) for p in points], fill, stroke, width, dash))

    def rect(self, x: float, y: float, w: float, h: float, fill: str = "none",
             stroke: str = INK, width: float = THIN, dash: str = "") -> None:
        self.polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill, stroke, width, dash)

    def text(self, point: Point, content: str, size: float = 10.0, anchor: str = "middle",
             color: str = INK, dy: float = 0.0, weight: str = "normal", title: str = "") -> None:
        x, y = self.px(point)
        self.items.append(Text((x, y + dy), content, size, anchor, color, weight, title=title))

    # ---- キャンバス座標に直接置く要素 ----
    def label_px(self, x: float, y: float, content: str, size: float = 10.0,
                 anchor: str = "start", color: str = INK, weight: str = "normal") -> None:
        self.items.append(Text((x, y), content, size, anchor, color, weight))

    def rect_px(self, x: float, y: float, w: float, h: float, fill: str = "none",
                stroke: str = INK, width: float = THIN) -> None:
        self.items.append(
            Polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill, stroke, width)
        )

    def line_px(self, x1: float, y1: float, x2: float, y2: float,
                width: float = THIN, color: str = INK) -> None:
        self.items.append(Line((x1, y1), (x2, y2), width, color))

    # ---- 寸法線 ----
    def _tick(self, point: Point) -> None:
        x, y = self.px(point)
        d = 3.5
        self.line_px(x - d, y + d, x + d, y - d, THIN, INK)

    def dim_h(self, x1: float, x2: float, y: float, label: Optional[str] = None,
              size: float = 9.0) -> None:
        """水平寸法線（y の高さに引く）。"""
        self.line((x1, y), (x2, y), THIN, INK)
        self._tick((x1, y))
        self._tick((x2, y))
        text = label if label is not None else f"{abs(x2 - x1) * 1000:.0f}"
        self.text(((x1 + x2) / 2, y), text, size, "middle", INK, dy=-4)

    def dim_v(self, y1: float, y2: float, x: float, label: Optional[str] = None,
              size: float = 9.0) -> None:
        """垂直寸法線（x の位置に引く）。"""
        self.line((x, y1), (x, y2), THIN, INK)
        self._tick((x, y1))
        self._tick((x, y2))
        text = label if label is not None else f"{abs(y2 - y1) * 1000:.0f}"
        px, py = self.px((x, (y1 + y2) / 2))
        self.items.append(Text((px - 4, py), text, size, "middle", INK, rotate=-90))

    def north_arrow(self, point: Point, size_m: float = 1.2) -> None:
        """方位記号（真北）。"""
        x, y = point
        self.polygon(
            [(x, y + size_m), (x - size_m * 0.35, y - size_m * 0.5), (x, y - size_m * 0.2),
             (x + size_m * 0.35, y - size_m * 0.5)],
            fill=INK, stroke=INK, width=THIN,
        )
        self.text((x, y + size_m), "N", 11, "middle", INK, dy=-6, weight="bold")

    def table(self, x_px: float, y_px: float, rows: Sequence[Tuple[str, str]],
              width_px: float = 190.0, size: float = 9.5) -> None:
        """面積表などの小さな表をキャンバス座標に描く。"""
        row_h = 15.0
        height = row_h * len(rows)
        self.rect_px(x_px, y_px, width_px, height, fill="#ffffff", stroke=INK, width=THIN)
        for index, (key, value) in enumerate(rows):
            top = y_px + row_h * index
            if index:
                self.line_px(x_px, top, x_px + width_px, top, THIN, GRAY)
            self.label_px(x_px + 6, top + row_h - 4, key, size)
            self.label_px(x_px + width_px - 6, top + row_h - 4, value, size, anchor="end")
        self.line_px(
            x_px + width_px * 0.55, y_px, x_px + width_px * 0.55, y_px + height, THIN, GRAY
        )

    # ---- 合成 ----
    def embed(self, other: "Canvas", x_px: float, y_px: float,
              scale: Optional[float] = None, box: Optional[Tuple[float, float]] = None,
              center: bool = True) -> float:
        """別のキャンバスを、このキャンバスの指定位置に取り込む。

        `box` を渡すとその枠（幅, 高さ）に収まるよう縮尺を決め、既定では枠内に
        中央寄せする。販売図面のように複数の図を 1 枚に組むために使う。
        戻り値は適用した縮尺。
        """
        offset_x = offset_y = 0.0
        if scale is None:
            if box is None:
                scale = 1.0
            else:
                scale = min(box[0] / other.width_px, box[1] / other.height_px)
        if box and center:
            offset_x = max(0.0, (box[0] - other.width_px * scale) / 2)
            offset_y = max(0.0, (box[1] - other.height_px * scale) / 2)

        def move(point: Point) -> Point:
            return (x_px + offset_x + point[0] * scale, y_px + offset_y + point[1] * scale)

        for item in other.items:
            if isinstance(item, Line):
                self.items.append(
                    Line(move(item.a), move(item.b), max(0.2, item.width * scale),
                         item.color, _scale_dash(item.dash, scale), item.title)
                )
            elif isinstance(item, Polygon):
                self.items.append(
                    Polygon([move(p) for p in item.points], item.fill, item.stroke,
                            max(0.2, item.width * scale), _scale_dash(item.dash, scale), item.close)
                )
            else:
                self.items.append(
                    Text(move(item.point), item.content, max(4.0, item.size * scale),
                         item.anchor, item.color, item.weight, item.rotate, item.title)
                )
        return scale

    # ---- 出力 ----
    def render(self) -> str:
        """SVG 文字列。"""
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width_px:.0f}" '
            f'height="{self.height_px:.0f}" viewBox="0 0 {self.width_px:.0f} {self.height_px:.0f}">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            f'<text x="10" y="20" font-size="13" font-family="sans-serif" font-weight="bold" '
            f'fill="{INK}">{self.title}</text>',
        ]
        for item in self.items:
            parts.append(_svg_item(item))
        parts.append(
            f'<text x="10" y="{self.height_px - 8:.0f}" font-size="9" font-family="sans-serif" '
            f'fill="{GRAY}">{self.subtitle}</text>'
        )
        parts.append("</svg>")
        return "\n".join(parts)

    def draw_on(self, page, origin_x: float, origin_y: float, scale: float = 1.0) -> None:
        """PDF ページに描画する。

        `origin_x` / `origin_y` はページ上での左下位置 [pt]。`scale` は px → pt の倍率。
        キャンバスは y 下向き、PDF は y 上向きのため上下を反転する。
        """
        height = self.height_px

        def to_pdf(point: Point) -> Point:
            x, y = point
            return (origin_x + x * scale, origin_y + (height - y) * scale)

        page.text(
            origin_x, origin_y + height * scale + 6, self.title, 11.5 * scale, rgb(INK)
        )
        for item in self.items:
            if isinstance(item, Line):
                (x1, y1), (x2, y2) = to_pdf(item.a), to_pdf(item.b)
                page.line(x1, y1, x2, y2, max(0.2, item.width * scale), rgb(item.color),
                          _dash(item.dash, scale))
            elif isinstance(item, Polygon):
                page.polygon(
                    [to_pdf(p) for p in item.points],
                    stroke_width=max(0.2, item.width * scale),
                    stroke=rgb(item.stroke) if item.stroke != "none" else None,
                    fill=rgb(item.fill) if item.fill != "none" else None,
                    close=item.close,
                    dash=_dash(item.dash, scale),
                )
            else:
                x, y = to_pdf(item.point)
                align = {"start": "left", "middle": "center", "end": "right"}[item.anchor]
                if item.rotate:
                    # SVG は y 下向きに回すため、PDF では符号が反転する
                    page.text(x, y, item.content, item.size * scale, rgb(item.color),
                              align, rotate=-item.rotate)
                else:
                    page.text(x, y - item.size * scale * 0.32, item.content,
                              item.size * scale, rgb(item.color), align)
        if self.subtitle:
            page.text(origin_x, origin_y - 10 * scale, self.subtitle, 8 * scale, rgb(GRAY))


def _scale_dash(dash: str, scale: float) -> str:
    if not dash:
        return ""
    return " ".join(f"{float(value) * scale:.2f}" for value in dash.split())


def _dash(dash: str, scale: float) -> Optional[List[float]]:
    if not dash:
        return None
    return [float(value) * scale for value in dash.split()]


def _svg_item(item: Primitive) -> str:
    if isinstance(item, Line):
        dash = f' stroke-dasharray="{item.dash}"' if item.dash else ""
        head = (
            f'<line x1="{item.a[0]:.1f}" y1="{item.a[1]:.1f}" x2="{item.b[0]:.1f}" '
            f'y2="{item.b[1]:.1f}" stroke="{item.color}" stroke-width="{item.width}"{dash}'
        )
        if item.title:
            return f"{head}><title>{item.title}</title></line>"
        return head + "/>"
    if isinstance(item, Polygon):
        dash = f' stroke-dasharray="{item.dash}"' if item.dash else ""
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in item.points)
        tag = "polygon" if item.close else "polyline"
        return (
            f'<{tag} points="{points}" fill="{item.fill}" stroke="{item.stroke}" '
            f'stroke-width="{item.width}"{dash}/>'
        )
    transform = ""
    if item.rotate:
        transform = f' transform="rotate({item.rotate:.0f} {item.point[0]:.1f} {item.point[1]:.1f})"'
    title = f"<title>{item.title}</title>" if item.title else ""
    return (
        f'<text x="{item.point[0]:.1f}" y="{item.point[1]:.1f}" font-size="{item.size:.1f}" '
        f'text-anchor="{item.anchor}" font-family="sans-serif" font-weight="{item.weight}" '
        f'fill="{item.color}"{transform}>{item.content}{title}</text>'
    )
