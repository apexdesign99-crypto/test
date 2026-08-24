"""平面幾何ユーティリティ（依存ライブラリなし）。

座標系はローカル平面直角座標 [m]。x は東方向、y は北方向を正とする。
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Polygon = List[Point]


def signed_area(polygon: Sequence[Point]) -> float:
    """符号付き面積。反時計回りで正。"""
    n = len(polygon)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def area(polygon: Sequence[Point]) -> float:
    """面積 [m2]。"""
    return abs(signed_area(polygon))


def is_ccw(polygon: Sequence[Point]) -> bool:
    return signed_area(polygon) > 0


def ensure_ccw(polygon: Sequence[Point]) -> Polygon:
    return list(polygon) if is_ccw(polygon) else list(reversed(polygon))


def centroid(polygon: Sequence[Point]) -> Point:
    a = signed_area(polygon)
    if abs(a) < 1e-12:
        xs = [p[0] for p in polygon] or [0.0]
        ys = [p[1] for p in polygon] or [0.0]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6 * a), cy / (6 * a))


def bbox(polygon: Sequence[Point]) -> Tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y)"""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def perimeter(polygon: Sequence[Point]) -> float:
    n = len(polygon)
    total = 0.0
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def shape_regularity(polygon: Sequence[Point]) -> float:
    """整形度 0..1。外接矩形に対する充填率（1.0 = 完全な矩形）。"""
    a = area(polygon)
    if a <= 0:
        return 0.0
    min_x, min_y, max_x, max_y = bbox(polygon)
    rect = (max_x - min_x) * (max_y - min_y)
    if rect <= 0:
        return 0.0
    return min(1.0, a / rect)


def rectangle(x: float, y: float, w: float, h: float) -> Polygon:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def scale_rect_to_area(rect: Sequence[Point], target_area: float) -> Polygon:
    """矩形を中心を保ったまま相似縮小し、目標面積に合わせる。"""
    a = area(rect)
    if a <= 0 or target_area <= 0:
        return list(rect)
    if target_area >= a:
        return list(rect)
    k = math.sqrt(target_area / a)
    cx, cy = centroid(rect)
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in rect]


def translate(polygon: Iterable[Point], dx: float, dy: float) -> Polygon:
    return [(x + dx, y + dy) for x, y in polygon]


def point_in_polygon(pt: Point, polygon: Sequence[Point]) -> bool:
    x, y = pt
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xint:
                inside = not inside
    return inside
