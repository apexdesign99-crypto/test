"""AI 間取り生成。

申請図面・BIM に載せられる水準にするため、次の3点を守って生成する。

1. **910mm グリッド**（半間）— 室の寸法・位置をすべてグリッド上に載せる。
   木造の実施設計はこのモジュールで進むため、半端寸法の平面は使えない。
2. **動線コアの階間整合** — 階段・ホール・便所を敷地の道路側に立てた
   幅1.82mのコア列にまとめ、階段の位置を全階で完全に一致させる。
   上下階で階段がずれた平面は、そのままでは実施設計に渡せない。
3. **開口部の生成** — 各室の外壁面に窓・玄関ドアを配置する。採光・換気の
   法規判定（`compliance.py`）、立面図（`drawings.py`）、IFC の
   IfcWindow / IfcDoor はすべてこの開口部を使う。

分割は面積比に基づく再帰二分割（slice & dice）をグリッドのセル数で行うため、
重なりなく敷き詰めつつ、すべての辺がグリッドに乗る。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import Point, Polygon, bbox, rectangle
from .svgkit import ACCENT, MEDIUM, THICK, Canvas
from .models import (
    Building,
    Direction,
    Envelope,
    Floor,
    Opening,
    Room,
    Site,
    Structure,
    is_habitable_name,
)

#: 基準グリッド（半間）[m]
GRID_M = 0.91
#: グリッド1マスの面積 [m2]
CELL_AREA_M2 = GRID_M * GRID_M
#: 1 畳 = 1.62 m2（中京間換算）
JO_M2 = 1.62
#: 動線コア列の幅（セル数）— 1.82m。階段幅+手すりの実寸に合わせる
CORE_WIDTH_CELLS = 2


@dataclass
class RoomSpec:
    """室の要求仕様。"""

    name: str
    weight: float  # 階内での面積配分比
    min_m2: float = 3.0
    max_m2: float = float("inf")  # 上限（余剰は上限のない室に回る）
    cells: Optional[int] = None  # コア列に置く室の長さ（セル数）


@dataclass
class FloorProgram:
    """1 フロアの室構成。"""

    core: List[RoomSpec]  # 動線コア（玄関/ホール → 階段 → 便所 → 収納）
    field: List[RoomSpec]  # 残りの居室・水回り

    @property
    def rooms(self) -> List[RoomSpec]:
        return self.core + self.field


def recommended_floor_area_m2(household_size: int) -> float:
    """誘導居住面積水準（一般型・戸建）相当の目標延床面積 [m2]。

    住生活基本計画の 25 x 人数 + 25（2人以上）を目安とする。
    """
    if household_size <= 1:
        return 55.0
    return 25.0 * household_size + 25.0


def program_for(storeys: int, household_size: int) -> Dict[int, FloorProgram]:
    """階数と家族構成から各階の室プログラムを決める。

    コア列の並び（玄関/ホール → 階段 → 便所）と各室のセル数は全階で共通に
    するため、階段が上下階で必ず一致する。
    """
    children = max(0, household_size - 2)
    program: Dict[int, FloorProgram] = {}

    program[1] = FloorProgram(
        core=[
            RoomSpec("玄関・ホール", 0.13, 3.3, 9.0, cells=3),
            RoomSpec("階段", 0.09, 2.6, 6.0, cells=3),
            RoomSpec("トイレ", 0.05, 1.5, 2.5, cells=1),
            RoomSpec("収納", 0.06, 1.5, 5.0, cells=None),
        ],
        field=[
            RoomSpec("LDK", 0.45, 16.0),  # 余剰面積は LDK が吸収する
            RoomSpec("浴室", 0.10, 3.0, 5.0),
            RoomSpec("洗面脱衣室", 0.08, 2.5, 5.0),
        ],
    )

    if storeys >= 2:
        upper_field: List[RoomSpec] = [
            RoomSpec("主寝室", 0.30, 10.0),  # 上階の余剰吸収室
            RoomSpec("ウォークインクローゼット", 0.09, 3.0, 8.0),
        ]
        rooms_on_2f = children if storeys == 2 else max(1, children - 1)
        for i in range(rooms_on_2f):
            upper_field.append(RoomSpec(f"洋室{i + 1}", 0.21, 7.0, 14.0))
        if rooms_on_2f == 0:
            upper_field.append(RoomSpec("書斎", 0.21, 6.0, 14.0))
        program[2] = FloorProgram(core=_upper_core(), field=upper_field)

    if storeys >= 3:
        third_field: List[RoomSpec] = [
            RoomSpec("納戸", 0.20, 4.0, 10.0),
            RoomSpec(f"洋室{max(1, children)}", 0.35, 7.0, 14.0),
            RoomSpec("フリースペース", 0.30, 6.0),  # 上限なし（余剰吸収）
        ]
        program[3] = FloorProgram(core=_upper_core(), field=third_field)

    return program


def _upper_core() -> List[RoomSpec]:
    """上階のコア列。1階と同じセル数にすることで階段位置が揃う。"""
    return [
        RoomSpec("ホール", 0.13, 3.3, 9.0, cells=3),
        RoomSpec("階段", 0.09, 2.6, 6.0, cells=3),
        RoomSpec("トイレ", 0.05, 1.5, 2.5, cells=1),
        RoomSpec("納戸", 0.06, 1.5, 5.0, cells=None),
    ]


def ldk_type(program: Dict[int, FloorProgram]) -> str:
    """間取りタイプ（例: 4LDK）。"""
    private = 0
    for floor_program in program.values():
        for spec in floor_program.rooms:
            if spec.name.startswith("洋室") or spec.name in ("主寝室", "書斎", "フリースペース"):
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


def _split_cells(
    specs: List[RoomSpec],
    areas: List[float],
    cx: int,
    cy: int,
    nx: int,
    ny: int,
) -> List[Tuple[RoomSpec, int, int, int, int]]:
    """セル矩形を面積比で再帰二分割する。戻り値は (spec, x, y, nx, ny)。"""
    if not specs or nx <= 0 or ny <= 0:
        return []
    if len(specs) == 1:
        return [(specs[0], cx, cy, nx, ny)]

    total = sum(areas) or 1.0
    best_index, best_diff = 1, float("inf")
    running = 0.0
    for i in range(1, len(specs)):
        running += areas[i - 1]
        diff = abs(running - total / 2)
        if diff < best_diff:
            best_diff, best_index = diff, i
    ratio = sum(areas[:best_index]) / total

    if nx >= ny:  # 長辺で切る
        if nx < 2:
            return [(specs[0], cx, cy, nx, ny)]
        n1 = min(max(1, round(nx * ratio)), nx - 1)
        return _split_cells(specs[:best_index], areas[:best_index], cx, cy, n1, ny) + _split_cells(
            specs[best_index:], areas[best_index:], cx + n1, cy, nx - n1, ny
        )
    if ny < 2:
        return [(specs[0], cx, cy, nx, ny)]
    n1 = min(max(1, round(ny * ratio)), ny - 1)
    return _split_cells(specs[:best_index], areas[:best_index], cx, cy, nx, n1) + _split_cells(
        specs[best_index:], areas[best_index:], cx, cy + n1, nx, ny - n1
    )


def _slice_row(
    specs: List[RoomSpec],
    areas: List[float],
    cx: int,
    cy: int,
    nx: int,
    ny: int,
) -> List[Tuple[RoomSpec, int, int, int, int]]:
    """1列の室を東西方向にセル数で按分する。各室は列の上下辺に接する。"""
    if not specs:
        return []
    # 居室は 1.82m（2セル）未満にしない。確保できない場合は再帰分割に委ねる
    min_w = 2 if all(is_habitable_name(spec.name) for spec in specs) else 1
    if nx < min_w * len(specs):
        return _split_cells(specs, areas, cx, cy, nx, ny)

    total = sum(areas) or 1.0
    widths: List[int] = []
    remaining = nx
    for index, area_value in enumerate(areas):
        if index == len(areas) - 1:
            widths.append(remaining)
            break
        reserve = min_w * (len(areas) - index - 1)  # 後続の室の最小幅を残す
        width = min(max(min_w, round(nx * area_value / total)), remaining - reserve)
        widths.append(width)
        remaining -= width

    placed: List[Tuple[RoomSpec, int, int, int, int]] = []
    offset = cx
    for spec, width in zip(specs, widths):
        placed.append((spec, offset, cy, width, ny))
        offset += width
    return placed


def _split_perimeter(
    specs: List[RoomSpec],
    areas: List[float],
    cx: int,
    cy: int,
    nx: int,
    ny: int,
) -> List[Tuple[RoomSpec, int, int, int, int]]:
    """居室が必ず外壁（南北面）に接するように配置する。

    奥行きのある区画は南列・北列の2列に分け、それぞれを東西に切る。
    こうすると中央に埋もれる室が出ず、全居室で採光が取れる。
    """
    if not specs:
        return []
    if len(specs) == 1:
        return [(specs[0], cx, cy, nx, ny)]
    if ny < 6 or len(specs) < 3:
        return _slice_row(specs, areas, cx, cy, nx, ny)

    # 面積の大きい室から、合計の小さい列へ振り分ける（南列に主要室が入る）
    south: List[int] = []
    north: List[int] = []
    south_area = north_area = 0.0
    for index in sorted(range(len(specs)), key=lambda i: -areas[i]):
        if south_area <= north_area:
            south.append(index)
            south_area += areas[index]
        else:
            north.append(index)
            north_area += areas[index]
    if not north:
        return _slice_row(specs, areas, cx, cy, nx, ny)

    south_cells = min(max(2, round(ny * south_area / (south_area + north_area))), ny - 2)
    return _slice_row(
        [specs[i] for i in south], [areas[i] for i in south], cx, cy, nx, south_cells
    ) + _slice_row(
        [specs[i] for i in north], [areas[i] for i in north], cx, cy + south_cells, nx,
        ny - south_cells,
    )


def footprint_cells(
    site: Site,
    envelope: Envelope,
    target_building_area_m2: float,
    aspect_cap: float = 1.7,
) -> Tuple[int, int, float, float]:
    """建物外形をグリッドのセル数で決める。

    戻り値は (nx, ny, 原点x, 原点y)。原点は敷地外接矩形と後退距離から求める。

    セル数は「法規上の上限（建蔽率・敷地）を超えない範囲で、目標建築面積に
    最も近い組み合わせ」を選ぶ。常に切り捨てると目標面積を1割近く下回るため。
    """
    min_x, min_y, max_x, max_y = bbox(site.polygon)
    setback = max(site.zoning.wall_setback_m, 0.5)
    avail_w = max(GRID_M * 3, (max_x - min_x) - setback * 2)
    avail_h = max(GRID_M * 3, (max_y - min_y) - setback * 2)

    # 超えてはいけない上限（法規と敷地）と、近づけたい目標を分けて扱う
    hard_limit = min(envelope.max_building_area_m2, avail_w * avail_h)
    target = min(target_building_area_m2, hard_limit)

    max_nx = max(2, int(avail_w // GRID_M))
    max_ny = max(2, int(avail_h // GRID_M))

    # 目標との差 → 敷地の縦横比との差 の順で最良の組み合わせを選ぶ
    site_ratio = avail_w / avail_h
    best_score: Optional[Tuple[float, float]] = None
    best_cells: Optional[Tuple[int, int]] = None
    for cx in range(2, max_nx + 1):
        for cy in range(2, max_ny + 1):
            ratio = cx / cy
            if ratio > aspect_cap or 1 / ratio > aspect_cap:
                continue
            if cx * cy * CELL_AREA_M2 > hard_limit + 1e-9:
                continue
            score = (abs(cx * cy * CELL_AREA_M2 - target), abs(ratio - site_ratio))
            if best_score is None or score < best_score:
                best_score, best_cells = score, (cx, cy)

    if best_cells is None:  # 縦横比の制約で候補が無い場合は素直に切り捨てる
        ny = max(2, min(max_ny, int(round(math.sqrt(target / CELL_AREA_M2 / site_ratio)))))
        best_cells = (max(2, min(max_nx, int(target / CELL_AREA_M2 / ny))), ny)

    nx, ny = best_cells
    width, height = nx * GRID_M, ny * GRID_M
    x0 = min_x + setback + max(0.0, (avail_w - width) / 2)
    y0 = min_y + setback + max(0.0, (avail_h - height) / 2)
    return nx, ny, x0, y0


def footprint_for(site: Site, envelope: Envelope, target_building_area_m2: float) -> Polygon:
    """グリッドに乗った建物外形（矩形）を返す。"""
    nx, ny, x0, y0 = footprint_cells(site, envelope, target_building_area_m2)
    return rectangle(x0, y0, nx * GRID_M, ny * GRID_M)


def plan_volume(
    envelope: Envelope,
    household_size: int,
    target_floor_area_m2: Optional[float] = None,
) -> Tuple[float, int]:
    """目標延床面積と階数を決める。

    法規上の上限（`envelope`）を超えない範囲で、家族構成に見合う規模を採用する。
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


def core_side(site: Site) -> Direction:
    """動線コア列を寄せる方位（原則として道路側）。"""
    road = site.widest_road
    if road is None:
        return Direction.E
    if road.direction in (Direction.E, Direction.W):
        return road.direction
    return Direction.E  # 南北道路のときは東側にコアを寄せ、南面を居室に空ける


def _core_slots(core: Sequence[RoomSpec], length_cells: int) -> List[int]:
    """コア列の各室に割り当てるセル数（全階で同じ結果になるよう決定的に決める）。"""
    slots = [spec.cells or 0 for spec in core]
    fixed = sum(slots)
    if fixed > length_cells:  # 短い建物では優先度順に削る
        for i in range(len(slots) - 1, -1, -1):
            while slots[i] > 1 and sum(slots) > length_cells:
                slots[i] -= 1
        while sum(slots) > length_cells and len(slots) > 1:
            slots.pop()
    remaining = length_cells - sum(slots)
    if remaining > 0:
        # 末尾（収納・納戸）で余りを吸収する
        slots[-1] += remaining
    return slots


def _place_core(
    core: Sequence[RoomSpec],
    slots: Sequence[int],
    side: Direction,
    nx: int,
    ny: int,
    entrance_at_south: bool,
) -> List[Tuple[RoomSpec, int, int, int, int]]:
    """コア列の室をセル座標に配置する。"""
    placed: List[Tuple[RoomSpec, int, int, int, int]] = []
    if side in (Direction.E, Direction.W):
        cx = nx - CORE_WIDTH_CELLS if side is Direction.E else 0
        offset = 0
        order = range(len(slots)) if entrance_at_south else reversed(range(len(slots)))
        for index in order:
            cells = slots[index]
            if cells <= 0:
                continue
            cy = offset if entrance_at_south else ny - offset - cells
            placed.append((core[index], cx, cy, CORE_WIDTH_CELLS, cells))
            offset += cells
    else:  # 北・南に寄せる（東西道路のときは使わないが対称に扱う）
        cy = ny - CORE_WIDTH_CELLS if side is Direction.N else 0
        offset = 0
        for index, cells in enumerate(slots):
            if cells <= 0:
                continue
            placed.append((core[index], offset, cy, cells, CORE_WIDTH_CELLS))
            offset += cells
    return placed


def _facades_of(room: Room, footprint: Polygon, eps: float = 1e-6) -> List[Direction]:
    """室が外壁に面している方位を返す。"""
    x0, y0, x1, y1 = bbox(footprint)
    facades: List[Direction] = []
    if abs(room.y - y0) < eps:
        facades.append(Direction.S)
    if abs(room.y + room.h - y1) < eps:
        facades.append(Direction.N)
    if abs(room.x - x0) < eps:
        facades.append(Direction.W)
    if abs(room.x + room.w - x1) < eps:
        facades.append(Direction.E)
    return facades


def _facade_span(room: Room, facade: Direction) -> Tuple[float, float]:
    """室が面する外壁の（開始位置, 長さ）。南北面は x、東西面は y。"""
    if facade in (Direction.S, Direction.N):
        return room.x, room.w
    return room.y, room.h


#: 採光に有利な方位の優先順
_FACADE_PRIORITY = {Direction.S: 0, Direction.E: 1, Direction.W: 2, Direction.N: 3}
def _grid_opening(
    span_start: float, span_length: float, origin: float, wanted_width: float
) -> Tuple[float, float]:
    """開口部の位置と幅をグリッドに合わせる。

    壁が 910mm 未満の細切れになると耐力壁として数えられないため、開口部は
    グリッド線上に置き、残る壁が必ずグリッドの整数倍になるようにする。
    戻り値は (開始位置, 幅)。
    """
    cells = max(1, int(round(span_length / GRID_M)))
    start_cell = round((span_start - origin) / GRID_M)
    wanted_cells = max(1, int(math.ceil(wanted_width / GRID_M - 1e-9)))
    # 最低 1 マスは壁を残す（両隣の室の壁と繋がるため実質はもっと長くなる）
    width_cells = min(wanted_cells, max(1, cells - 1))
    offset = (cells - width_cells) // 2
    return origin + (start_cell + offset) * GRID_M, width_cells * GRID_M


#: 掃出窓の高さ [m]（腰高 0）
SLIDING_H = 2.0
#: 腰窓の高さ [m] と腰高 [m]
CASEMENT_H, CASEMENT_SILL = 1.2, 0.8


def place_openings(
    floor: Floor,
    entrance_room: Optional[str],
    entrance_facade: Optional[Direction],
) -> List[Opening]:
    """各室の外壁面に窓・玄関ドアを配置する。

    居室には採光に必要な面積（床面積の 1/7）を満たす窓を、水回りには換気用の
    小窓を、玄関には道路側の玄関ドアを置く。位置と幅はグリッドに合わせるため、
    残った壁は 910mm の整数倍になり、そのまま耐力壁として数えられる。

    居室の窓は 1 面で足りなければ次の外壁面へ回し、それでも足りないときは
    腰窓を掃出窓（高さ 2.0m）に上げて必要面積を満たす。どの面でも最低 1 マス
    （910mm）の壁を残すため、四分割法の壁量はグリッド単位で確保される。
    """
    x0, y0, _, _ = bbox(floor.footprint)
    openings: List[Opening] = []

    for room in floor.rooms:
        facades = _facades_of(room, floor.footprint)
        if not facades:
            continue
        facades.sort(key=lambda f: _FACADE_PRIORITY[f])
        facade = facades[0]
        start, length = _facade_span(room, facade)
        origin = x0 if facade in (Direction.S, Direction.N) else y0
        if length < GRID_M * 2:  # 1 マス分の壁も残せない狭い面には開口を設けない
            continue

        if room.name == entrance_room and entrance_facade in facades:
            start, length = _facade_span(room, entrance_facade)
            origin = x0 if entrance_facade in (Direction.S, Direction.N) else y0
            position, width = _grid_opening(start, length, origin, 0.91)
            openings.append(
                Opening("玄関ドア", room.name, floor.storey, entrance_facade,
                        position, width, 2.0, 0.0)
            )
            continue

        if room.is_habitable:
            # 採光に必要な面積（令 20 条の採光補正を見込まず、余裕 1.2 倍で見る）
            required = room.area_m2 / 7.0 * 1.2
            placed: List[Opening] = []
            for side in facades:
                if sum(o.width * o.height for o in placed) >= required - 1e-9:
                    break
                span_start, span_length = _facade_span(room, side)
                if span_length < GRID_M * 2:
                    continue
                side_origin = x0 if side in (Direction.S, Direction.N) else y0
                sliding = side is Direction.S
                height = SLIDING_H if sliding else CASEMENT_H
                deficit = required - sum(o.width * o.height for o in placed)
                position, width = _grid_opening(
                    span_start, span_length, side_origin, deficit / height
                )
                placed.append(
                    Opening(
                        "掃出窓" if sliding else "窓",
                        room.name, floor.storey, side, position, width, height,
                        0.0 if sliding else CASEMENT_SILL,
                    )
                )
            # どの面でも幅が足りない場合は、腰窓を掃出窓に上げて高さで稼ぐ
            for index, opening in enumerate(placed):
                if sum(o.width * o.height for o in placed) >= required - 1e-9:
                    break
                if opening.height >= SLIDING_H:
                    continue
                placed[index] = Opening(
                    "掃出窓", opening.room, opening.storey, opening.facade,
                    opening.position, opening.width, SLIDING_H, 0.0,
                )
            openings.extend(placed)
        elif room.name in ("浴室", "洗面脱衣室", "トイレ", "階段", "ホール", "家事室"):
            position, width = _grid_opening(start, length, origin, GRID_M)
            openings.append(
                Opening("窓", room.name, floor.storey, facade, position, width, 0.9, 1.2)
            )
    return openings


def generate(
    site: Site,
    envelope: Envelope,
    household_size: int = 4,
    structure: Structure = Structure.WOOD,
    floor_height_m: float = 2.9,
    target_floor_area_m2: Optional[float] = None,
    ceiling_height_m: float = 2.4,
) -> Building:
    """間取り・開口部付きの建物案を生成する。"""
    target_area, storeys = plan_volume(envelope, household_size, target_floor_area_m2)
    if storeys == 0 or target_area <= 0:
        return Building(
            structure=structure, floors=[], total_floor_area_m2=0.0, height_m=0.0, ldk_type="-"
        )

    program = program_for(storeys, household_size)
    nx, ny, x0, y0 = footprint_cells(site, envelope, target_area / storeys)
    footprint = rectangle(x0, y0, nx * GRID_M, ny * GRID_M)
    floor_area = nx * ny * CELL_AREA_M2

    side = core_side(site)
    road = site.widest_road
    entrance_at_south = not (road is not None and road.direction is Direction.N)
    entrance_facade = road.direction if road is not None else Direction.S

    # コア列のセル割りは 1 階で決め、全階で使い回す（階段位置を揃えるため）
    core_length = ny if side in (Direction.E, Direction.W) else nx
    slots = _core_slots(program[1].core, core_length)

    floors: List[Floor] = []
    remaining = min(target_area, envelope.max_floor_area_m2)
    for storey in range(1, storeys + 1):
        if remaining < floor_area * 0.5:
            break
        floor_program = program.get(storey, program[max(program)])
        placed = _place_core(floor_program.core, slots, side, nx, ny, entrance_at_south)

        # コア列を除いた残りの矩形に居室・水回りを敷き詰める
        if side in (Direction.E, Direction.W):
            field_x = CORE_WIDTH_CELLS if side is Direction.W else 0
            field_nx, field_ny, field_y = nx - CORE_WIDTH_CELLS, ny, 0
        else:
            field_x, field_y = 0, CORE_WIDTH_CELLS if side is Direction.S else 0
            field_nx, field_ny = nx, ny - CORE_WIDTH_CELLS
        field_specs = list(floor_program.field)
        field_area_total = field_nx * field_ny * CELL_AREA_M2
        rough = _allocate_areas(field_specs, field_area_total)

        # 水回り・収納はコア側の帯にまとめ、居室は外周側に置いて採光を確保する
        service = [i for i, spec in enumerate(field_specs) if not is_habitable_name(spec.name)]
        habitable = [i for i, spec in enumerate(field_specs) if is_habitable_name(spec.name)]
        service_cells = 0
        if service and habitable:
            column_area = field_ny * CELL_AREA_M2
            # ユニットバス（1.82m角）が納まる場合のみ最低2セル幅を確保する
            min_cells = 2 if any(field_specs[i].name == "浴室" for i in service) else 1
            cap_cells = max(min_cells, math.ceil(sum(field_specs[i].max_m2 for i in service) / column_area))
            service_cells = min(
                max(min_cells, round(sum(rough[i] for i in service) / column_area)),
                cap_cells,
                max(min_cells, field_nx - 2),
            )
        elif service:
            service_cells = field_nx

        if side is Direction.W:  # コアが西側 → 水回りは区画の西端
            service_x, habitable_x = field_x, field_x + service_cells
        else:  # コアが東側 → 水回りは区画の東端
            service_x, habitable_x = field_x + (field_nx - service_cells), field_x

        if service_cells:
            strip_specs = [field_specs[i] for i in service]
            strip_area = service_cells * field_ny * CELL_AREA_M2
            capacity = sum(spec.max_m2 for spec in strip_specs)
            if strip_area > capacity + 1.5:
                # 帯が余る場合は家事室（上階は収納）を立てて上限超過を防ぐ
                strip_specs.append(
                    RoomSpec("家事室" if storey == 1 else "クローゼット", 0.15, 2.5)
                )
            # 縦長の帯になるため、長辺（南北）方向に切って水回りを積む
            placed += _split_cells(
                strip_specs,
                _allocate_areas(strip_specs, strip_area),
                service_x,
                field_y,
                service_cells,
                field_ny,
            )
        if habitable:
            hab_specs = [field_specs[i] for i in habitable]
            hab_cells = field_nx - service_cells
            placed += _split_perimeter(
                hab_specs,
                _allocate_areas(hab_specs, hab_cells * field_ny * CELL_AREA_M2),
                habitable_x,
                field_y,
                hab_cells,
                field_ny,
            )

        order = {spec.name: i for i, spec in enumerate(floor_program.rooms)}
        # 生成時に追加した室（家事室など）は末尾に置く
        rooms = [
            Room(spec.name, x0 + cx * GRID_M, y0 + cy * GRID_M,
                 w * GRID_M, h * GRID_M, storey)
            for spec, cx, cy, w, h in placed
        ]
        rooms.sort(key=lambda r: order.get(r.name, 99))

        floor = Floor(
            storey=storey,
            footprint=footprint,
            rooms=rooms,
            height_m=floor_height_m,
            ceiling_height_m=ceiling_height_m,
        )
        floor.openings = place_openings(
            floor,
            entrance_room="玄関・ホール" if storey == 1 else None,
            entrance_facade=entrance_facade if storey == 1 else None,
        )
        floors.append(floor)
        remaining -= floor_area

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


def plan_canvas(site: Site, building: Building, storey: int = 1, scale: float = 26.0) -> Canvas:
    """指定階の平面図を `Canvas` として組み立てる（SVG / PDF 共通）。

    室の寸法に応じて文字サイズを落とし、面積表記の省略と室名の切り詰めを行う。
    完全な室名は SVG のツールチップに残す。
    """
    floor = next((f for f in building.floors if f.storey == storey), None)
    if floor is None:
        raise ValueError(f"{storey}階は存在しない")

    min_x, min_y, max_x, max_y = bbox(site.polygon)
    canvas = Canvas(
        min_x, min_y, max_x, max_y,
        scale=scale,
        margin_m=1.5,
        title=f"{storey}階 平面図　{floor.area_m2:.1f}m² （{building.ldk_type}）　S=1:100 相当",
        subtitle="自動生成（確認申請用の下書き）／建築士による確認が必要",
    )
    canvas.polygon(site.polygon, fill="#f4f1ea", stroke="#8a8577", width=MEDIUM, dash="6 4")

    for room in floor.rooms:
        canvas.rect(room.x, room.y, room.w, room.h, fill="#ffffff", stroke="#333333", width=THICK)
        w_px, h_px = room.w * scale, room.h * scale
        font = max(7.0, min(12.0, min(w_px, h_px) / 5.5))
        show_area = h_px >= font * 3.2 and w_px >= font * 5
        center = (room.x + room.w / 2, room.y + room.h / 2)
        canvas.text(
            center, _fit_label(room.name, w_px - 4, font), font, "middle", "#222222",
            dy=-4 if show_area else font / 3,
            title=f"{room.name} {room.jo:.1f}帖 / {room.area_m2:.1f}m²",
        )
        if show_area:
            canvas.text(
                center, f"{room.jo:.1f}帖 / {room.area_m2:.1f}m²", font * 0.8, "middle",
                "#666666", dy=font,
            )

    # 開口部（外壁線上に太線で表現）
    fx0, fy0, fx1, fy1 = bbox(floor.footprint)
    for opening in floor.openings:
        color = ACCENT if opening.kind == "玄関ドア" else "#4f7f9c"
        if opening.facade in (Direction.S, Direction.N):
            y = fy0 if opening.facade is Direction.S else fy1
            a, b = (opening.position, y), (opening.position + opening.width, y)
        else:
            x = fx0 if opening.facade is Direction.W else fx1
            a, b = (x, opening.position), (x, opening.position + opening.width)
        canvas.line(
            a, b, 5, color,
            title=f"{opening.kind} {opening.room} "
                  f"W{opening.width * 1000:.0f}×H{opening.height * 1000:.0f}",
        )
    return canvas


def to_svg(site: Site, building: Building, storey: int = 1, scale: float = 26.0) -> str:
    """指定階の平面図を SVG 文字列で返す（1m = `scale` px）。開口部も描画する。"""
    return plan_canvas(site, building, storey, scale).render()
