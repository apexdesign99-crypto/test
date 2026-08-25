"""作図用の最小 SVG キット。

建築図面に必要な要素（線・矩形・寸法線・引出線・方位記号・図面枠）だけを備える。
ワールド座標は「x 右、y 上」の実寸 [m]。SVG は y が下向きなので Canvas 側で反転する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]

THIN = 0.8
MEDIUM = 1.6
THICK = 2.6
INK = "#1d1b17"
GRAY = "#8a8577"
ACCENT = "#2f6f4f"


@dataclass
class Canvas:
    """ワールド座標 [m] を SVG に描くためのキャンバス。"""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    scale: float = 24.0  # 1m あたりの px
    margin_m: float = 2.0
    title: str = ""
    subtitle: str = ""
    parts: List[str] = field(default_factory=list)

    @property
    def width_px(self) -> float:
        return (self.max_x - self.min_x + self.margin_m * 2) * self.scale

    @property
    def height_px(self) -> float:
        return (self.max_y - self.min_y + self.margin_m * 2) * self.scale + 34

    def px(self, point: Point) -> Point:
        """ワールド座標 → SVG 座標。"""
        x, y = point
        return (
            (x - self.min_x + self.margin_m) * self.scale,
            (self.max_y - y + self.margin_m) * self.scale + 34,
        )

    # ---- 基本図形 ----
    def line(self, a: Point, b: Point, width: float = THIN, color: str = INK, dash: str = "") -> None:
        ax, ay = self.px(a)
        bx, by = self.px(b)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
            f'stroke="{color}" stroke-width="{width}"{dash_attr}/>'
        )

    def polyline(self, points: Sequence[Point], width: float = THIN, color: str = INK,
                 dash: str = "", fill: str = "none") -> None:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (self.px(p) for p in points))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<polyline points="{pts}" fill="{fill}" stroke="{color}" '
            f'stroke-width="{width}"{dash_attr}/>'
        )

    def polygon(self, points: Sequence[Point], fill: str = "none", stroke: str = INK,
                width: float = THIN, dash: str = "") -> None:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (self.px(p) for p in points))
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{width}"{dash_attr}/>'
        )

    def rect(self, x: float, y: float, w: float, h: float, fill: str = "none",
             stroke: str = INK, width: float = THIN, dash: str = "") -> None:
        self.polygon(
            [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill, stroke, width, dash
        )

    def text(self, point: Point, content: str, size: float = 10.0, anchor: str = "middle",
             color: str = INK, dy: float = 0.0, weight: str = "normal") -> None:
        x, y = self.px(point)
        self.parts.append(
            f'<text x="{x:.1f}" y="{y + dy:.1f}" font-size="{size:.1f}" text-anchor="{anchor}" '
            f'font-family="sans-serif" font-weight="{weight}" fill="{color}">{content}</text>'
        )

    def label_px(self, x: float, y: float, content: str, size: float = 10.0,
                 anchor: str = "start", color: str = INK, weight: str = "normal") -> None:
        """SVG 座標に直接置くラベル（凡例・面積表など）。"""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" text-anchor="{anchor}" '
            f'font-family="sans-serif" font-weight="{weight}" fill="{color}">{content}</text>'
        )

    # ---- 寸法線 ----
    def _tick(self, point: Point, vertical: bool) -> None:
        x, y = self.px(point)
        d = 3.5
        if vertical:
            x1, y1, x2, y2 = x - d, y + d, x + d, y - d
        else:
            x1, y1, x2, y2 = x - d, y + d, x + d, y - d
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{INK}" stroke-width="{THIN}"/>'
        )

    def dim_h(self, x1: float, x2: float, y: float, label: Optional[str] = None,
              size: float = 9.0) -> None:
        """水平寸法線（y の高さに引く）。"""
        self.line((x1, y), (x2, y), THIN, INK)
        self._tick((x1, y), False)
        self._tick((x2, y), False)
        text = label if label is not None else f"{abs(x2 - x1) * 1000:.0f}"
        self.text(((x1 + x2) / 2, y), text, size, "middle", INK, dy=-4)

    def dim_v(self, y1: float, y2: float, x: float, label: Optional[str] = None,
              size: float = 9.0) -> None:
        """垂直寸法線（x の位置に引く）。"""
        self.line((x, y1), (x, y2), THIN, INK)
        self._tick((x, y1), True)
        self._tick((x, y2), True)
        text = label if label is not None else f"{abs(y2 - y1) * 1000:.0f}"
        px, py = self.px((x, (y1 + y2) / 2))
        self.parts.append(
            f'<text x="{px - 4:.1f}" y="{py:.1f}" font-size="{size:.1f}" text-anchor="middle" '
            f'font-family="sans-serif" fill="{INK}" transform="rotate(-90 {px - 4:.1f} {py:.1f})">'
            f"{text}</text>"
        )

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
        """面積表などの小さな表を SVG 座標に描く。"""
        row_h = 15.0
        height = row_h * len(rows)
        self.parts.append(
            f'<rect x="{x_px:.1f}" y="{y_px:.1f}" width="{width_px:.1f}" height="{height:.1f}" '
            f'fill="#ffffff" stroke="{INK}" stroke-width="{THIN}"/>'
        )
        for index, (key, value) in enumerate(rows):
            top = y_px + row_h * index
            if index:
                self.parts.append(
                    f'<line x1="{x_px:.1f}" y1="{top:.1f}" x2="{x_px + width_px:.1f}" '
                    f'y2="{top:.1f}" stroke="{GRAY}" stroke-width="{THIN}"/>'
                )
            self.label_px(x_px + 6, top + row_h - 4, key, size)
            self.label_px(x_px + width_px - 6, top + row_h - 4, value, size, anchor="end")
        self.parts.append(
            f'<line x1="{x_px + width_px * 0.55:.1f}" y1="{y_px:.1f}" '
            f'x2="{x_px + width_px * 0.55:.1f}" y2="{y_px + height:.1f}" '
            f'stroke="{GRAY}" stroke-width="{THIN}"/>'
        )

    def render(self) -> str:
        header = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width_px:.0f}" '
            f'height="{self.height_px:.0f}" viewBox="0 0 {self.width_px:.0f} {self.height_px:.0f}">'
            f'<rect width="100%" height="100%" fill="#ffffff"/>'
        )
        title = (
            f'<text x="10" y="20" font-size="13" font-family="sans-serif" font-weight="bold" '
            f'fill="{INK}">{self.title}</text>'
            f'<text x="10" y="{self.height_px - 8:.0f}" font-size="9" font-family="sans-serif" '
            f'fill="{GRAY}">{self.subtitle}</text>'
        )
        return "\n".join([header, title, *self.parts, "</svg>"])
