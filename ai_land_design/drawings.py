"""確認申請用の図面生成。

建築確認申請に必要な図面のうち、本ツールの情報だけで機械的に描けるものを
SVG で出力する。

    配置図      敷地境界・道路・後退距離・建物位置・方位・面積表
    平面図      `layout.to_svg`（間取り・開口部）
    立面図      4面（外形・屋根・開口部・高さ寸法・道路斜線）
    断面図      階高・天井高・軒高・最高の高さ
    求積図      敷地求積（三角形分割）・各階床面積求積

いずれも申請図書の下書きであり、そのまま提出できるものではない。
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from .feasibility import height_limits
from .geometry import Point, Polygon, area, bbox
from .models import Building, Direction, Envelope, Floor, Opening, Site
from .svgkit import ACCENT, GRAY, INK, MEDIUM, THICK, THIN, Canvas

#: 屋根勾配（4寸勾配相当）
ROOF_PITCH = 0.4
#: 基礎の立ち上がり [m]
FOUNDATION_M = 0.45
#: 軒の出 [m]
EAVES_M = 0.6

DISCLAIMER = "自動生成（確認申請用の下書き）／建築士による確認が必要"


def _scale_for(width_m: float, height_m: float, max_px: float = 900.0) -> float:
    span = max(width_m, height_m, 1.0)
    return max(12.0, min(46.0, max_px / (span + 4)))


def site_plan_svg(site: Site, building: Building, envelope: Envelope) -> str:
    """配置図。"""
    sx0, sy0, sx1, sy1 = bbox(site.polygon)
    canvas = Canvas(
        sx0 - 3, sy0 - 3, sx1 + 3, sy1 + 3,
        scale=_scale_for(sx1 - sx0 + 6, sy1 - sy0 + 6),
        margin_m=1.0,
        title=f"配置図　S=1:100 相当　{site.address or site.site_id}",
        subtitle=DISCLAIMER,
    )

    # 道路
    for road in site.roads:
        if road.direction is Direction.S:
            canvas.rect(sx0, sy0 - road.width_m, sx1 - sx0, road.width_m, fill="#f0eee9", stroke=GRAY)
            canvas.text(((sx0 + sx1) / 2, sy0 - road.width_m / 2), f"道路 幅員 {road.width_m:.1f}m", 10)
        elif road.direction is Direction.N:
            canvas.rect(sx0, sy1, sx1 - sx0, road.width_m, fill="#f0eee9", stroke=GRAY)
            canvas.text(((sx0 + sx1) / 2, sy1 + road.width_m / 2), f"道路 幅員 {road.width_m:.1f}m", 10)
        elif road.direction is Direction.W:
            canvas.rect(sx0 - road.width_m, sy0, road.width_m, sy1 - sy0, fill="#f0eee9", stroke=GRAY)
            canvas.text((sx0 - road.width_m / 2, (sy0 + sy1) / 2), f"道路 {road.width_m:.1f}m", 10)
        else:
            canvas.rect(sx1, sy0, road.width_m, sy1 - sy0, fill="#f0eee9", stroke=GRAY)
            canvas.text((sx1 + road.width_m / 2, (sy0 + sy1) / 2), f"道路 {road.width_m:.1f}m", 10)

        # 42条2項道路のセットバック線
        if road.is_setback_road or road.width_m < 4.0:
            depth = max(0.0, (4.0 - road.width_m) / 2)
            if depth > 0:
                if road.direction is Direction.S:
                    canvas.line((sx0, sy0 + depth), (sx1, sy0 + depth), MEDIUM, ACCENT, "8 4")
                    canvas.text(((sx0 + sx1) / 2, sy0 + depth), f"セットバック {depth:.2f}m", 9, dy=-4, color=ACCENT)
                elif road.direction is Direction.N:
                    canvas.line((sx0, sy1 - depth), (sx1, sy1 - depth), MEDIUM, ACCENT, "8 4")
                elif road.direction is Direction.W:
                    canvas.line((sx0 + depth, sy0), (sx0 + depth, sy1), MEDIUM, ACCENT, "8 4")
                else:
                    canvas.line((sx1 - depth, sy0), (sx1 - depth, sy1), MEDIUM, ACCENT, "8 4")

    # 敷地境界と辺長
    canvas.polygon(site.polygon, fill="#fbfaf7", stroke=INK, width=MEDIUM)
    points = list(site.polygon)
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        canvas.text(mid, f"{length * 1000:.0f}", 9, color=GRAY, dy=-3)

    # 建物と後退寸法
    if building.floors:
        footprint = building.floors[0].footprint
        bx0, by0, bx1, by1 = bbox(footprint)
        canvas.polygon(footprint, fill="#e8f1eb", stroke=INK, width=THICK)
        canvas.text(
            ((bx0 + bx1) / 2, (by0 + by1) / 2),
            f"計画建物 {building.structure.value} {building.storeys}階建",
            11, weight="bold",
        )
        canvas.text(
            ((bx0 + bx1) / 2, (by0 + by1) / 2),
            f"建築面積 {building.footprint_area_m2:.2f}m²", 9, dy=14, color=GRAY,
        )
        # 4方向の後退距離
        canvas.dim_v(sy0, by0, bx0 - 0.6, f"{(by0 - sy0) * 1000:.0f}")
        canvas.dim_v(by1, sy1, bx0 - 0.6, f"{(sy1 - by1) * 1000:.0f}")
        canvas.dim_h(sx0, bx0, by0 - 0.6, f"{(bx0 - sx0) * 1000:.0f}")
        canvas.dim_h(bx1, sx1, by0 - 0.6, f"{(sx1 - bx1) * 1000:.0f}")
        # 建物寸法
        canvas.dim_h(bx0, bx1, sy1 + 1.2, f"{(bx1 - bx0) * 1000:.0f}")
        canvas.dim_v(by0, by1, sx1 + 1.2, f"{(by1 - by0) * 1000:.0f}")

    canvas.north_arrow((sx1 + 1.8, sy1 - 1.0))
    canvas.table(
        canvas.width_px - 210,
        canvas.height_px - 130,
        [
            ("敷地面積", f"{site.area_m2:.2f} m²"),
            ("建築面積", f"{building.footprint_area_m2:.2f} m²"),
            ("建蔽率", f"{building.footprint_area_m2 / envelope.effective_site_area_m2 * 100:.1f}%"
             if envelope.effective_site_area_m2 else "—"),
            ("延べ面積", f"{building.total_floor_area_m2:.2f} m²"),
            ("容積率", f"{building.total_floor_area_m2 / envelope.effective_site_area_m2 * 100:.1f}%"
             if envelope.effective_site_area_m2 else "—"),
            ("用途地域", site.zoning.use_district.value),
        ],
    )
    return canvas.render()


def _facade_geometry(building: Building, facade: Direction) -> Tuple[float, float, float]:
    """立面の (見付け幅, 奥行, 軒高)。"""
    footprint = building.floors[0].footprint
    x0, y0, x1, y1 = bbox(footprint)
    width = (x1 - x0) if facade in (Direction.S, Direction.N) else (y1 - y0)
    depth = (y1 - y0) if facade in (Direction.S, Direction.N) else (x1 - x0)
    eaves = sum(f.height_m for f in building.floors)
    return width, depth, eaves


def elevation_svg(site: Site, building: Building, facade: Direction) -> str:
    """立面図（1面）。屋根・開口部・高さ寸法・道路斜線を描く。"""
    if not building.floors:
        raise ValueError("建物が生成されていない")

    width, depth, eaves = _facade_geometry(building, facade)
    ridge = eaves + (depth / 2 * ROOF_PITCH if building.roof == "切妻" else 0.3)
    footprint = building.floors[0].footprint
    fx0, fy0, fx1, fy1 = bbox(footprint)
    origin = fx0 if facade in (Direction.S, Direction.N) else fy0

    canvas = Canvas(
        -2.0, -1.2, width + 3.5, ridge + 2.0,
        scale=_scale_for(width + 6, ridge + 4),
        margin_m=1.0,
        title=f"{facade.value}立面図　S=1:100 相当",
        subtitle=DISCLAIMER,
    )

    # GL
    canvas.line((-1.5, 0), (width + 2.0, 0), THICK, INK)
    canvas.text((width + 2.0, 0), "GL", 10, "start", INK, dy=14)

    # 外壁
    canvas.rect(0, 0, width, eaves, fill="#fbfaf7", stroke=INK, width=MEDIUM)
    # 基礎
    canvas.rect(0, -FOUNDATION_M, width, FOUNDATION_M, fill="#efece5", stroke=GRAY)

    # 屋根
    if building.roof == "切妻":
        if facade in (Direction.S, Direction.N):
            # 平側：屋根面は軒先（下辺）から棟（上辺）までの矩形に投影される
            canvas.polygon(
                [(-EAVES_M, eaves), (width + EAVES_M, eaves),
                 (width + EAVES_M, ridge), (-EAVES_M, ridge)],
                fill="#d8cfc0", stroke=INK, width=MEDIUM,
            )
            canvas.text((width / 2, ridge), "棟", 9, color=INK, dy=-4)
        else:  # 妻側：三角形
            canvas.polygon(
                [(-EAVES_M, eaves), (width / 2, ridge), (width + EAVES_M, eaves)],
                fill="#d8cfc0", stroke=INK, width=MEDIUM,
            )
    else:
        canvas.rect(0, eaves, width, 0.3, fill="#d8cfc0", stroke=INK, width=MEDIUM)

    # 各階の床レベル
    level = 0.0
    for floor in building.floors:
        if level > 0:
            canvas.line((0, level), (width, level), THIN, GRAY, "5 4")
            canvas.text((0.2, level), f"{floor.storey}FL", 9, "start", GRAY, dy=-3)
        level += floor.height_m

    # 開口部
    level = 0.0
    for floor in building.floors:
        for opening in floor.openings:
            if opening.facade is not facade:
                continue
            x = opening.position - origin
            if facade in (Direction.N, Direction.W):  # 反対側から見るため左右反転
                x = width - (x + opening.width)
            y = level + opening.sill_m + 0.4  # 床レベル + 窓台（FL+400 を標準とする）
            color = ACCENT if opening.kind == "玄関ドア" else "#4f7f9c"
            canvas.rect(x, y if opening.kind != "玄関ドア" else level,
                        opening.width, opening.height,
                        fill="#eef3f6", stroke=color, width=MEDIUM)
        level += floor.height_m

    # 高さ寸法
    canvas.dim_v(0, eaves, width + 0.9, f"軒高 {eaves * 1000:.0f}")
    canvas.dim_v(0, ridge, width + 2.2, f"最高 {ridge * 1000:.0f}")
    canvas.dim_h(0, width, -0.9, f"{width * 1000:.0f}")

    # 道路斜線（道路に面する立面のみ）
    road = site.widest_road
    if road is not None and road.direction is facade:
        setback = (fy0 - bbox(site.polygon)[1]) if facade is Direction.S else 0.5
        limits = height_limits(site, wall_setback_m=max(setback, 0.3), building_depth_m=depth)
        road_limit = next((l for l in limits if l.name == "道路斜線制限"), None)
        if road_limit and road_limit.limit_m < 999:
            slope = 1.25 if site.zoning.use_district.is_residential_group else 1.5
            base_x = -(road.width_m + setback)  # 道路の反対側境界の位置
            top = ridge + 1.5  # 図面に収まる高さの上限
            x_from = max(canvas.min_x + 0.2, base_x)
            x_to = min(width + 2.0, base_x + top / slope)
            if x_to > x_from + 0.3:
                canvas.line(
                    (x_from, max(0.0, (x_from - base_x) * slope)),
                    (x_to, (x_to - base_x) * slope),
                    THIN, "#b03a2e", "7 4",
                )
                canvas.text((x_to, (x_to - base_x) * slope),
                            f"道路斜線 勾配{slope}", 9, "end", "#b03a2e", dy=-4)
            else:
                # 斜線が図面の範囲より高い位置にある（余裕が大きい）場合は注記で示す
                canvas.text((width / 2, ridge + 1.2),
                            f"道路斜線制限 {road_limit.limit_m:.2f}m（最高の高さ "
                            f"{ridge:.2f}m ／ 余裕 {road_limit.limit_m - ridge:.2f}m）",
                            9, "middle", "#b03a2e")
    return canvas.render()


def all_elevations_svg(site: Site, building: Building) -> Dict[str, str]:
    """4面の立面図。"""
    return {d.value: elevation_svg(site, building, d) for d in
            (Direction.S, Direction.E, Direction.N, Direction.W)}


def section_svg(site: Site, building: Building) -> str:
    """断面図（南北方向）。階高・天井高・軒高・最高の高さを示す。"""
    if not building.floors:
        raise ValueError("建物が生成されていない")
    footprint = building.floors[0].footprint
    x0, y0, x1, y1 = bbox(footprint)
    depth = y1 - y0
    eaves = sum(f.height_m for f in building.floors)
    ridge = eaves + (depth / 2 * ROOF_PITCH if building.roof == "切妻" else 0.3)

    canvas = Canvas(
        -2.0, -1.5, depth + 4.0, ridge + 2.0,
        scale=_scale_for(depth + 6, ridge + 4),
        margin_m=1.0,
        title="断面図（南北方向）　S=1:100 相当",
        subtitle=DISCLAIMER,
    )

    canvas.line((-1.5, 0), (depth + 2.0, 0), THICK, INK)
    canvas.text((depth + 2.0, 0), "GL", 10, "start", INK, dy=14)
    canvas.rect(0, -FOUNDATION_M, depth, FOUNDATION_M, fill="#efece5", stroke=GRAY)

    level = 0.0
    for floor in building.floors:
        canvas.rect(0, level, depth, floor.height_m, fill="#ffffff", stroke=INK, width=MEDIUM)
        ceiling = level + floor.ceiling_height_m
        canvas.line((0, ceiling), (depth, ceiling), THIN, GRAY, "5 4")
        canvas.text((depth / 2, level + floor.height_m / 2), f"{floor.storey}階", 11)
        canvas.text((depth / 2, level + floor.height_m / 2),
                    f"天井高 {floor.ceiling_height_m * 1000:.0f}", 9, dy=14, color=GRAY)
        canvas.dim_v(level, level + floor.height_m, -0.9, f"{floor.height_m * 1000:.0f}")
        level += floor.height_m

    if building.roof == "切妻":
        canvas.polygon(
            [(-EAVES_M, eaves), (depth / 2, ridge), (depth + EAVES_M, eaves)],
            fill="#d8cfc0", stroke=INK, width=MEDIUM,
        )
    else:
        canvas.rect(0, eaves, depth, 0.3, fill="#d8cfc0", stroke=INK, width=MEDIUM)

    canvas.dim_v(0, eaves, depth + 1.2, f"軒高 {eaves * 1000:.0f}")
    canvas.dim_v(0, ridge, depth + 2.6, f"最高の高さ {ridge * 1000:.0f}")
    canvas.dim_h(0, depth, -1.1, f"{depth * 1000:.0f}")
    return canvas.render()


def _triangulate(polygon: Sequence[Point]) -> List[Tuple[Point, Point, Point]]:
    """凸多角形を扇状に三角形分割する（求積図用）。"""
    points = list(polygon)
    return [(points[0], points[i], points[i + 1]) for i in range(1, len(points) - 1)]


def _triangle_area(triangle: Tuple[Point, Point, Point]) -> float:
    (x1, y1), (x2, y2), (x3, y3) = triangle
    return abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2


def area_calculation_svg(site: Site, building: Building) -> str:
    """求積図。敷地は三角形分割、各階床面積は矩形の寸法から求積する。"""
    sx0, sy0, sx1, sy1 = bbox(site.polygon)
    canvas = Canvas(
        sx0 - 1.5, sy0 - 2.5, sx1 + 8.0, sy1 + 1.5,
        scale=_scale_for(sx1 - sx0 + 12, sy1 - sy0 + 6),
        margin_m=1.0,
        title="求積図（敷地・建築面積・床面積）　S=1:100 相当",
        subtitle=DISCLAIMER,
    )

    canvas.polygon(site.polygon, fill="#fbfaf7", stroke=INK, width=MEDIUM)
    triangles = _triangulate(site.polygon)
    rows: List[Tuple[str, str]] = []
    for index, triangle in enumerate(triangles, start=1):
        canvas.polygon(list(triangle), fill="none", stroke=ACCENT, width=THIN, dash="6 3")
        center = (
            sum(p[0] for p in triangle) / 3,
            sum(p[1] for p in triangle) / 3,
        )
        canvas.text(center, f"△{index}", 10, color=ACCENT)
        base = math.hypot(triangle[1][0] - triangle[0][0], triangle[1][1] - triangle[0][1])
        value = _triangle_area(triangle)
        height = 2 * value / base if base else 0.0
        rows.append((f"△{index}  {base:.3f} × {height:.3f} ÷ 2", f"{value:.3f} m²"))
    rows.append(("敷地面積 合計", f"{area(site.polygon):.2f} m²"))

    if building.floors:
        footprint = building.floors[0].footprint
        bx0, by0, bx1, by1 = bbox(footprint)
        canvas.polygon(footprint, fill="#e8f1eb", stroke=INK, width=THICK)
        canvas.dim_h(bx0, bx1, by0 - 0.8, f"{(bx1 - bx0) * 1000:.0f}")
        canvas.dim_v(by0, by1, bx1 + 0.8, f"{(by1 - by0) * 1000:.0f}")
        rows.append(
            (f"建築面積  {bx1 - bx0:.3f} × {by1 - by0:.3f}", f"{building.footprint_area_m2:.2f} m²")
        )
        for floor in building.floors:
            fx0, fy0, fx1, fy1 = bbox(floor.footprint)
            rows.append(
                (f"{floor.storey}階床面積  {fx1 - fx0:.3f} × {fy1 - fy0:.3f}", f"{floor.area_m2:.2f} m²")
            )
        rows.append(("延べ面積 合計", f"{building.total_floor_area_m2:.2f} m²"))

    canvas.table(canvas.width_px - 300, 60, rows, width_px=290)
    canvas.north_arrow((sx1 + 1.2, sy1 - 0.5))
    return canvas.render()


def all_drawings(site: Site, building: Building, envelope: Envelope) -> Dict[str, str]:
    """申請図書に使う図面一式（ファイル名 → SVG）。"""
    from . import layout

    drawings: Dict[str, str] = {"site_plan.svg": site_plan_svg(site, building, envelope)}
    if not building.floors:
        return drawings
    for floor in building.floors:
        drawings[f"plan_{floor.storey}f.svg"] = layout.to_svg(site, building, floor.storey)
    for name, svg in all_elevations_svg(site, building).items():
        drawings[f"elevation_{name}.svg"] = svg
    drawings["section.svg"] = section_svg(site, building)
    drawings["area_calculation.svg"] = area_calculation_svg(site, building)
    return drawings
