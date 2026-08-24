"""AI 間取り生成。

建築可能ボリューム（建築面積・延床面積・階数）と家族構成から
室構成（プログラム）を決め、各階の footprint を再帰分割して
重なりなく敷き詰めた矩形の部屋配置を生成する。

分割は面積比に基づく再帰二分割（slice & dice）。長辺方向に切るため、
極端に細長い部屋が生じにくい。生成結果は SVG に描画できる。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .geometry import Point, Polygon, bbox, centroid, rectangle, scale_rect_to_area
from .models import Building, Envelope, Floor, Room, Site, Structure

#: 1 畳 = 1.62 m2（中京間換算）
JO_M2 = 1.62


@dataclass
class RoomSpec:
    """室の要求仕様。"""

    name: str
    weight: float  # 階内での面積配分比
    min_m2: float = 3.0
    max_m2: float = float("inf")  # 上限（余剰は上限のない室に回る）


def recommended_floor_area_m2(household_size: int) -> float:
    """誘導居住面積水準（一般型・戸建）相当の目標延床面積 [m2]。

    住生活基本計画の 25 x 人数 + 25（2人以上）を目安とする。
    """
    if household_size <= 1:
        return 55.0
    return 25.0 * household_size + 25.0


def program_for(storeys: int, household_size: int) -> Dict[int, List[RoomSpec]]:
    """階数と家族構成から各階の室プログラムを決める。"""
    children = max(0, household_size - 2)
    program: Dict[int, List[RoomSpec]] = {}

    program[1] = [
        RoomSpec("玄関・ホール", 0.13, 3.3, 9.0),
        RoomSpec("LDK", 0.45, 16.0),  # 余剰面積は LDK が吸収する
        RoomSpec("浴室", 0.10, 3.0, 5.0),
        RoomSpec("洗面脱衣室", 0.08, 2.5, 5.0),
        RoomSpec("トイレ", 0.05, 1.5, 2.5),
        RoomSpec("階段", 0.09, 2.6, 5.0),
        RoomSpec("収納", 0.10, 2.0, 7.0),
    ]

    if storeys >= 2:
        upper: List[RoomSpec] = [
            RoomSpec("主寝室", 0.30, 10.0, 22.0),
            RoomSpec("ウォークインクローゼット", 0.09, 3.0, 8.0),
            RoomSpec("ホール・階段", 0.13, 3.3, 10.0),
            RoomSpec("トイレ", 0.06, 1.5, 2.5),
        ]
        rooms_on_2f = children if storeys == 2 else max(1, children - 1)
        for i in range(rooms_on_2f):
            upper.append(RoomSpec(f"洋室{i + 1}", 0.21, 7.0, 14.0))
        if rooms_on_2f == 0:
            upper.append(RoomSpec("書斎", 0.21, 6.0, 14.0))
        program[2] = upper

    if storeys >= 3:
        third: List[RoomSpec] = [
            RoomSpec("ホール・階段", 0.15, 3.3, 10.0),
            RoomSpec("納戸", 0.20, 4.0, 10.0),
        ]
        remaining = max(1, children - (children - 1))
        for i in range(remaining):
            third.append(RoomSpec(f"洋室{children - remaining + i + 1}", 0.35, 7.0, 14.0))
        third.append(RoomSpec("フリースペース", 0.30, 6.0))
        program[3] = third

    return program


def ldk_type(program: Dict[int, List[RoomSpec]]) -> str:
    """間取りタイプ（例: 4LDK）。"""
    private = 0
    for rooms in program.values():
        for r in rooms:
            if r.name.startswith("洋室") or r.name in ("主寝室", "書斎", "納戸", "フリースペース"):
                if r.name not in ("納戸",):
                    private += 1
    return f"{private}LDK"


def _allocate_areas(specs: Sequence[RoomSpec], total_area: float) -> List[float]:
    """重み・最低面積・上限面積を考慮して各室の面積を決める。

    上限のある室（水回りなど）が飽和した場合、余剰は上限のない室（LDK 等）に回る。
    総面積が最低面積の合計に満たない場合は全体を比例縮小する。
    """
    if not specs:
        return []
    min_total = sum(s.min_m2 for s in specs)
    if total_area <= min_total:
        k = total_area / min_total if min_total > 0 else 0.0
        return [s.min_m2 * k for s in specs]

    weight_sum = sum(s.weight for s in specs) or 1.0
    areas = [total_area * s.weight / weight_sum for s in specs]

    for _ in range(12):
        areas = [min(max(a, s.min_m2), s.max_m2) for a, s in zip(areas, specs)]
        diff = total_area - sum(areas)
        if abs(diff) < 1e-6:
            break
        if diff > 0:
            donors = [i for i, s in enumerate(specs) if areas[i] < s.max_m2 - 1e-9]
        else:
            donors = [i for i, s in enumerate(specs) if areas[i] > s.min_m2 + 1e-9]
        if not donors:
            break
        donor_weight = sum(specs[i].weight for i in donors) or 1.0
        for i in donors:
            areas[i] += diff * specs[i].weight / donor_weight
    return areas


def _split(
    specs: List[RoomSpec],
    areas: List[float],
    x: float,
    y: float,
    w: float,
    h: float,
    storey: int,
) -> List[Room]:
    """矩形を面積比で再帰二分割し、部屋の矩形を確定する。"""
    if not specs:
        return []
    if len(specs) == 1:
        return [Room(specs[0].name, x, y, w, h, storey)]

    total = sum(areas)
    # 面積の累積が半分に最も近い位置で 2 グループに分ける。
    best_index, best_diff = 1, float("inf")
    running = 0.0
    for i in range(1, len(specs)):
        running += areas[i - 1]
        diff = abs(running - total / 2)
        if diff < best_diff:
            best_diff, best_index = diff, i
    first_area = sum(areas[:best_index])
    ratio = first_area / total if total > 0 else 0.5

    if w >= h:  # 長辺で切る
        w1 = w * ratio
        return _split(specs[:best_index], areas[:best_index], x, y, w1, h, storey) + _split(
            specs[best_index:], areas[best_index:], x + w1, y, w - w1, h, storey
        )
    h1 = h * ratio
    return _split(specs[:best_index], areas[:best_index], x, y, w, h1, storey) + _split(
        specs[best_index:], areas[best_index:], x, y + h1, w, h - h1, storey
    )


def footprint_for(
    site: Site,
    envelope: Envelope,
    target_building_area_m2: float | None = None,
    aspect_cap: float = 1.8,
) -> Polygon:
    """敷地の外接矩形から後退距離を引き、目標建築面積に合わせた矩形を返す。"""
    min_x, min_y, max_x, max_y = bbox(site.polygon)
    setback = max(site.zoning.wall_setback_m, 0.5)
    w = max(3.0, (max_x - min_x) - setback * 2)
    h = max(3.0, (max_y - min_y) - setback * 2)

    # 極端に細長い区画では建物の縦横比を抑える。
    if w / h > aspect_cap:
        w = h * aspect_cap
    elif h / w > aspect_cap:
        h = w * aspect_cap

    rect = rectangle(min_x + setback, min_y + setback, w, h)
    target = min(envelope.max_building_area_m2, w * h)
    if target_building_area_m2 is not None:
        target = min(target, target_building_area_m2)
    rect = scale_rect_to_area(rect, target)

    # 敷地の外接矩形内に収める。
    rx0, ry0, rx1, ry1 = bbox(rect)
    dx = max(0.0, min_x + setback - rx0) - max(0.0, rx1 - (max_x - setback))
    dy = max(0.0, min_y + setback - ry0) - max(0.0, ry1 - (max_y - setback))
    return [(px + dx, py + dy) for px, py in rect]


def plan_volume(
    envelope: Envelope,
    household_size: int,
    target_floor_area_m2: float | None = None,
) -> Tuple[float, int]:
    """目標延床面積と階数を決める。

    法規上の上限（`envelope`）を超えない範囲で、家族構成に見合う規模を採用する。
    上限が目標を下回る場合は上限側に張り付く。
    """
    target = target_floor_area_m2 or recommended_floor_area_m2(household_size)
    target = min(target, envelope.max_floor_area_m2)
    if envelope.max_building_area_m2 <= 0:
        return 0.0, 0
    needed = math.ceil(target / envelope.max_building_area_m2 - 1e-9)
    # 戸建住宅では敷地を建蔽率いっぱいに使い切るより、庭・駐車場を残して
    # 2 階建てとするのが一般的。75m2 を超える規模は 2 階建てを標準とする。
    preferred = 2 if target >= 75.0 else 1
    storeys = max(1, min(envelope.max_storeys, max(needed, preferred)))
    return target, storeys


def generate(
    site: Site,
    envelope: Envelope,
    household_size: int = 4,
    structure: Structure = Structure.WOOD,
    floor_height_m: float = 2.9,
    target_floor_area_m2: float | None = None,
) -> Building:
    """間取り付きの建物案を生成する。"""
    target_area, storeys = plan_volume(envelope, household_size, target_floor_area_m2)
    if storeys == 0 or target_area <= 0:
        return Building(
            structure=structure,
            floors=[],
            total_floor_area_m2=0.0,
            height_m=0.0,
            ldk_type="-",
        )

    program = program_for(storeys, household_size)
    per_floor = target_area / storeys
    base = footprint_for(site, envelope, target_building_area_m2=per_floor)
    base_w = base[1][0] - base[0][0]
    base_h = base[2][1] - base[1][1]
    base_area = base_w * base_h
    min_x, min_y, _, _ = bbox(base)

    floors: List[Floor] = []
    remaining = min(target_area, envelope.max_floor_area_m2)
    for storey in range(1, storeys + 1):
        area_this = min(base_area, remaining)
        if area_this < 15.0:
            break
        ratio = area_this / base_area if base_area > 0 else 1.0
        w = base_w * math.sqrt(ratio)
        h = base_h * math.sqrt(ratio)
        footprint = rectangle(min_x, min_y, w, h)

        specs = program.get(storey, program[max(program)])
        areas = _allocate_areas(specs, w * h)
        # 面積の大きい室から分割すると、細長い室が生じにくい。
        ordered = sorted(zip(specs, areas), key=lambda t: -t[1])
        rooms = _split(
            [s for s, _ in ordered], [a for _, a in ordered], min_x, min_y, w, h, storey
        )
        order = {s.name: i for i, s in enumerate(specs)}
        rooms.sort(key=lambda r: order.get(r.name, 99))
        floors.append(Floor(storey=storey, footprint=footprint, rooms=rooms, height_m=floor_height_m))
        remaining -= area_this

    total_area = sum(f.area_m2 for f in floors)
    height = min(len(floors) * floor_height_m + 1.6, envelope.max_height_m)
    return Building(
        structure=structure,
        floors=floors,
        total_floor_area_m2=total_area,
        height_m=height,
        ldk_type=ldk_type({k: v for k, v in program.items() if k <= len(floors)}),
        roof="切妻",
    )


def _fit_label(name: str, width_px: float, font_px: float) -> str:
    """室名が矩形の幅に収まらない場合に切り詰める。"""
    max_chars = max(1, int(width_px / font_px))
    if len(name) <= max_chars:
        return name
    return name[: max(1, max_chars - 1)] + "…"


def to_svg(site: Site, building: Building, storey: int = 1, scale: float = 26.0) -> str:
    """指定階の平面図を SVG 文字列で返す（1m = `scale` px）。

    小さい室ではラベルが枠からはみ出すため、室の寸法に応じて文字サイズを落とし、
    面積表記の省略と室名の切り詰めを行う。完全な室名は `<title>` に残す。
    """
    floor = next((f for f in building.floors if f.storey == storey), None)
    if floor is None:
        raise ValueError(f"{storey}階は存在しない")

    min_x, min_y, max_x, max_y = bbox(site.polygon)
    margin = 1.5
    width = (max_x - min_x + margin * 2) * scale
    height = (max_y - min_y + margin * 2) * scale

    def px(p: Point) -> Tuple[float, float]:
        # SVG は y 下向きのため反転する
        return ((p[0] - min_x + margin) * scale, (max_y - p[1] + margin) * scale)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    site_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (px(p) for p in site.polygon))
    parts.append(
        f'<polygon points="{site_pts}" fill="#f4f1ea" stroke="#8a8577" stroke-width="1.5" '
        'stroke-dasharray="6 4"/>'
    )

    for room in floor.rooms:
        x0, y0 = px((room.x, room.y + room.h))
        w_px, h_px = room.w * scale, room.h * scale
        parts.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w_px:.1f}" '
            f'height="{h_px:.1f}" fill="#ffffff" stroke="#333333" stroke-width="2"/>'
        )

        cx, cy = px((room.x + room.w / 2, room.y + room.h / 2))
        font = max(7.0, min(12.0, min(w_px, h_px) / 5.5))
        show_area = h_px >= font * 3.2 and w_px >= font * 5
        label = _fit_label(room.name, w_px - 4, font)
        name_y = cy - 4 if show_area else cy + font / 3
        parts.append(
            f'<text x="{cx:.1f}" y="{name_y:.1f}" font-size="{font:.1f}" text-anchor="middle" '
            f'font-family="sans-serif" fill="#222222">{label}'
            f'<title>{room.name} {room.jo:.1f}帖 / {room.area_m2:.1f}m²</title></text>'
        )
        if show_area:
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + font:.1f}" font-size="{font * 0.8:.1f}" '
                f'text-anchor="middle" font-family="sans-serif" fill="#666666">'
                f'{room.jo:.1f}帖 / {room.area_m2:.1f}m²</text>'
            )

    parts.append(
        f'<text x="8" y="18" font-size="13" font-family="sans-serif" fill="#111111">'
        f'{storey}階 平面図　{floor.area_m2:.1f}m² （{building.ldk_type}）</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)
