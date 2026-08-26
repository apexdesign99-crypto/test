"""木造軸組構法の壁量計算（令46条4項）。

確認申請で求められる壁量計算書に対応する。算定するのは次の3つ。

1. **必要壁量**
   - 地震力に対して: 床面積 × 係数（屋根の重さ・階数で決まる表）
   - 風圧力に対して: 見付面積 × 係数（原則 50 cm/m²）
2. **存在壁量** — 耐力壁の実長 × 壁倍率（方向別）
3. **配置のバランス（四分割法）** — 各階・各方向で側端部分（1/4）の
   充足率を求め、壁率比が 0.5 以上であることを確認する

.. warning::
   地震力の係数表は法改正で見直される。既定値は改正前（令和7年3月31日まで）の
   令46条4項 表2 の値であり、**令和7年4月施行の改正後は数値が異なる**。
   最新の値は `SeismicTable` を差し替えて使うこと。差し替えていない場合、
   判定は「要確認」として返す。

   耐力壁の配置は本来設計者が決めるものであり、ここでは外周壁と一定長さ以上の
   間仕切壁を耐力壁と仮定した概算である。実施設計では伏図に基づいて算定すること。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geometry import bbox
from .models import Building, Direction, Floor, Room

#: 見付面積から除く高さ（各階の床面から 1.35m 以下は算入しない）
WIND_EXCLUDED_HEIGHT_M = 1.35
#: 風圧力に対する必要壁量の係数 [cm/m²]（特定行政庁が定める区域では 50〜75）
WIND_COEFFICIENT = 50.0
#: 屋根勾配（drawings と揃える）
ROOF_PITCH = 0.4
#: 軒の出 [m]
EAVES_M = 0.6
#: 壁の実長として数えない最小長さ [m]（0.91m 未満の壁は耐力壁として数えない）
MIN_WALL_SEGMENT_M = 0.91


@dataclass(frozen=True)
class SeismicTable:
    """地震力に対する必要壁量の係数表 [cm/m²]。

    `values` のキーは (階数, 対象の階, 屋根の重さ)。屋根の重さは "軽い" / "重い"。
    """

    name: str
    effective: str
    values: Dict[Tuple[int, int, str], float]
    verified: bool = False  # 最新の告示値であることを利用者が確認済みか
    note: str = ""

    def coefficient(self, storeys: int, storey: int, roof: str) -> float:
        try:
            return self.values[(storeys, storey, roof)]
        except KeyError as error:
            raise ValueError(
                f"係数表に該当がありません（{storeys}階建の{storey}階・{roof}屋根）"
            ) from error


#: 令46条4項 表2（改正前・令和7年3月31日まで）の値
TABLE_LEGACY = SeismicTable(
    name="令46条4項 表2（改正前）",
    effective="令和7年3月31日まで",
    values={
        (1, 1, "軽い"): 11.0,
        (1, 1, "重い"): 15.0,
        (2, 1, "軽い"): 29.0,
        (2, 1, "重い"): 33.0,
        (2, 2, "軽い"): 15.0,
        (2, 2, "重い"): 21.0,
        (3, 1, "軽い"): 46.0,
        (3, 1, "重い"): 50.0,
        (3, 2, "軽い"): 34.0,
        (3, 2, "重い"): 39.0,
        (3, 3, "軽い"): 18.0,
        (3, 3, "重い"): 24.0,
    },
    verified=False,
    note=(
        "令和7年4月施行の改正で必要壁量の算定方法が見直されている。"
        "最新の告示値に差し替えて再計算すること。"
    ),
)


def confirm_table(table: SeismicTable) -> SeismicTable:
    """係数表を「現行の告示値であることを確認済み」として扱う。

    改正の反映は利用者（建築士）の確認に委ねる。確認済みにすると、
    壁量の判定が「要確認」から「適合／不適合」に変わる。
    """
    return SeismicTable(
        name=table.name,
        effective=table.effective,
        values=dict(table.values),
        verified=True,
        note=table.note,
    )


@dataclass
class WallLine:
    """1 本の耐力壁（方向・実長・壁倍率）。"""

    axis: str  # "X"（南北面に平行）/ "Y"（東西面に平行）
    position: float  # 直交方向の座標 [m]
    start: float
    end: float
    magnification: float
    kind: str  # "外壁" / "間仕切壁"

    @property
    def length_m(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def effective_cm(self) -> float:
        """存在壁量への寄与 [cm]。"""
        return self.length_m * 100 * self.magnification

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "kind": self.kind,
            "position": round(self.position, 3),
            "length_m": round(self.length_m, 3),
            "magnification": self.magnification,
            "effective_cm": round(self.effective_cm, 1),
        }


@dataclass
class DirectionResult:
    """1 階・1 方向分の壁量計算結果。"""

    axis: str
    required_seismic_cm: float
    required_wind_cm: float
    existing_cm: float
    elevation_area_m2: float
    quarter_ratio: Optional[float] = None  # 壁率比（四分割法）
    quarter_detail: Dict[str, float] = field(default_factory=dict)

    @property
    def required_cm(self) -> float:
        """地震力・風圧力のうち大きい方が必要壁量になる。"""
        return max(self.required_seismic_cm, self.required_wind_cm)

    @property
    def governing(self) -> str:
        return "地震力" if self.required_seismic_cm >= self.required_wind_cm else "風圧力"

    @property
    def ratio(self) -> float:
        """充足率（1.0 以上で必要壁量を満たす）。"""
        return self.existing_cm / self.required_cm if self.required_cm else 0.0

    @property
    def ok(self) -> bool:
        return self.ratio >= 1.0

    @property
    def balance_ok(self) -> bool:
        """四分割法の壁率比 0.5 以上。"""
        return self.quarter_ratio is None or self.quarter_ratio >= 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "required_seismic_cm": round(self.required_seismic_cm, 1),
            "required_wind_cm": round(self.required_wind_cm, 1),
            "required_cm": round(self.required_cm, 1),
            "governing": self.governing,
            "existing_cm": round(self.existing_cm, 1),
            "ratio": round(self.ratio, 3),
            "ok": self.ok,
            "elevation_area_m2": round(self.elevation_area_m2, 2),
            "quarter_ratio": round(self.quarter_ratio, 3) if self.quarter_ratio is not None else None,
            "balance_ok": self.balance_ok,
            "quarter_detail": {k: round(v, 3) for k, v in self.quarter_detail.items()},
        }


@dataclass
class FloorResult:
    storey: int
    floor_area_m2: float
    directions: List[DirectionResult]
    walls: List[WallLine] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(d.ok and d.balance_ok for d in self.directions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storey": self.storey,
            "floor_area_m2": round(self.floor_area_m2, 2),
            "ok": self.ok,
            "directions": [d.to_dict() for d in self.directions],
            "walls": [w.to_dict() for w in self.walls],
        }


@dataclass
class WallQuantityReport:
    """壁量計算の結果。"""

    floors: List[FloorResult]
    roof_weight: str
    table: SeismicTable
    magnifications: Dict[str, float]

    @property
    def ok(self) -> bool:
        """壁量・配置バランスの両方を満たすか。"""
        return self.quantity_ok and self.balance_ok

    @property
    def quantity_ok(self) -> bool:
        """必要壁量を満たすか。"""
        return all(d.ok for f in self.floors for d in f.directions)

    @property
    def balance_ok(self) -> bool:
        """四分割法による配置バランスを満たすか。"""
        return all(d.balance_ok for f in self.floors for d in f.directions)

    @property
    def worst_balance(self) -> Optional[float]:
        ratios = [
            d.quarter_ratio for f in self.floors for d in f.directions if d.quarter_ratio is not None
        ]
        return min(ratios) if ratios else None

    @property
    def verified(self) -> bool:
        """判定を確定できるか（係数表が最新であると確認済みか）。"""
        return self.table.verified

    @property
    def worst_ratio(self) -> float:
        ratios = [d.ratio for f in self.floors for d in f.directions]
        return min(ratios) if ratios else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "quantity_ok": self.quantity_ok,
            "balance_ok": self.balance_ok,
            "verified": self.verified,
            "worst_ratio": round(self.worst_ratio, 3),
            "worst_balance": round(self.worst_balance, 3) if self.worst_balance is not None else None,
            "roof_weight": self.roof_weight,
            "table": {
                "name": self.table.name,
                "effective": self.table.effective,
                "verified": self.table.verified,
                "note": self.table.note,
            },
            "magnifications": self.magnifications,
            "floors": [f.to_dict() for f in self.floors],
        }


def elevation_area_above(
    building: Building, storey: int, axis: str, roof_pitch: float = ROOF_PITCH
) -> float:
    """指定階の見付面積 [m²]。

    `axis` は風を受ける壁の方向。"X" は南北面（桁行方向の風）、"Y" は東西面。
    各階の床面から 1.35m 以下の部分は算入しない（令46条4項）。
    """
    if not building.floors:
        return 0.0
    x0, y0, x1, y1 = bbox(building.floors[0].footprint)
    width = (x1 - x0) if axis == "X" else (y1 - y0)
    depth = (y1 - y0) if axis == "X" else (x1 - x0)

    eaves = sum(f.height_m for f in building.floors)
    ridge = eaves + (depth / 2 * roof_pitch if building.roof == "切妻" else 0.3)

    base = sum(f.height_m for f in building.floors if f.storey < storey)
    lower = base + WIND_EXCLUDED_HEIGHT_M  # この高さより上を算入する

    # 壁部分（軒高まで）
    wall = width * max(0.0, eaves - lower)

    # 屋根部分：平側は矩形、妻側は三角形に投影される
    roof_height = ridge - eaves
    overhang = width + EAVES_M * 2
    if roof_height <= 0:
        roof = 0.0
    elif building.roof == "切妻" and axis == "X":
        # 妻側（東西面）に見える三角形かどうかは棟の向きで決まる。
        # 本ツールは棟を東西方向に架けるため、X 方向の面は平側＝矩形。
        roof = overhang * roof_height
    elif building.roof == "切妻":
        roof = overhang * roof_height / 2  # 妻側の三角形
    else:
        roof = overhang * roof_height
    return wall + roof


def _segments_along(
    rooms: Sequence[Room], axis: str, footprint_bounds: Tuple[float, float, float, float]
) -> Dict[float, List[Tuple[float, float]]]:
    """室の境界から、方向別の壁の位置と範囲を集める。

    "X" は x 軸に平行な壁（y = 一定）、"Y" は y 軸に平行な壁（x = 一定）。
    """
    lines: Dict[float, List[Tuple[float, float]]] = {}
    for room in rooms:
        if axis == "X":
            edges = [(round(room.y, 3), (room.x, room.x + room.w)),
                     (round(room.y + room.h, 3), (room.x, room.x + room.w))]
        else:
            edges = [(round(room.x, 3), (room.y, room.y + room.h)),
                     (round(room.x + room.w, 3), (room.y, room.y + room.h))]
        for position, span in edges:
            lines.setdefault(position, []).append(span)
    return lines


def _merge(spans: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """重なり・連続する区間をまとめる。"""
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def wall_lines(
    floor: Floor,
    axis: str,
    exterior_magnification: float = 2.5,
    interior_magnification: float = 1.0,
) -> List[WallLine]:
    """階の平面から、方向別の耐力壁を拾い出す。

    外周壁は開口部の幅を差し引き、間仕切壁は一定長さ以上のものを数える。
    既定の壁倍率は外壁 2.5（筋かい＋構造用面材相当）、間仕切壁 1.0
    （構造用面材片面張り相当）。実際の耐力壁の位置と仕様は設計者が決めるため、
    ここでは概算として扱う。
    """
    x0, y0, x1, y1 = bbox(floor.footprint)
    bounds = (x0, y0, x1, y1)
    exterior_positions = {round(y0, 3), round(y1, 3)} if axis == "X" else {round(x0, 3), round(x1, 3)}

    # 外壁の開口部（この方向の面にあるもの）を控除するための一覧
    openings: Dict[float, List[Tuple[float, float]]] = {}
    for opening in floor.openings:
        if axis == "X" and opening.facade in (Direction.S, Direction.N):
            position = round(y0 if opening.facade is Direction.S else y1, 3)
        elif axis == "Y" and opening.facade in (Direction.W, Direction.E):
            position = round(x0 if opening.facade is Direction.W else x1, 3)
        else:
            continue
        openings.setdefault(position, []).append(
            (opening.position, opening.position + opening.width)
        )

    walls: List[WallLine] = []
    for position, spans in _segments_along(floor.rooms, axis, bounds).items():
        is_exterior = position in exterior_positions
        magnification = exterior_magnification if is_exterior else interior_magnification
        for start, end in _merge(spans):
            pieces = [(start, end)]
            for hole_start, hole_end in sorted(openings.get(position, [])):
                remaining: List[Tuple[float, float]] = []
                for piece_start, piece_end in pieces:
                    if hole_end <= piece_start or hole_start >= piece_end:
                        remaining.append((piece_start, piece_end))
                        continue
                    if hole_start > piece_start:
                        remaining.append((piece_start, hole_start))
                    if hole_end < piece_end:
                        remaining.append((hole_end, piece_end))
                pieces = remaining
            for piece_start, piece_end in pieces:
                if piece_end - piece_start >= MIN_WALL_SEGMENT_M - 1e-6:
                    walls.append(
                        WallLine(
                            axis=axis,
                            position=position,
                            start=piece_start,
                            end=piece_end,
                            magnification=magnification,
                            kind="外壁" if is_exterior else "間仕切壁",
                        )
                    )
    return walls


def quarter_check(
    floor: Floor,
    axis: str,
    walls: Sequence[WallLine],
    required_per_m2_cm: float,
) -> Tuple[Optional[float], Dict[str, float]]:
    """四分割法（告示1352号）による配置バランスの確認。

    側端部分（両端から 1/4）ごとに 存在壁量 / 必要壁量 を求め、
    小さい方 ÷ 大きい方（壁率比）を返す。0.5 以上で適合。
    """
    x0, y0, x1, y1 = bbox(floor.footprint)
    if axis == "X":
        low, high, span = y0, y1, x1 - x0  # 側端は y 方向に取る
    else:
        low, high, span = x0, x1, y1 - y0
    quarter = (high - low) / 4
    if quarter <= 0:
        return None, {}

    zones = {
        "側端A": (low, low + quarter),
        "側端B": (high - quarter, high),
    }
    ratios: Dict[str, float] = {}
    for name, (zone_low, zone_high) in zones.items():
        existing = sum(
            wall.effective_cm
            for wall in walls
            if zone_low - 1e-6 <= wall.position <= zone_high + 1e-6
        )
        required = quarter * span * required_per_m2_cm  # 側端部分の床面積 × 係数
        ratios[name] = existing / required if required else 0.0

    values = list(ratios.values())
    if max(values) <= 0:
        return 0.0, ratios
    if min(values) >= 1.0:
        # 両側端とも充足率 1.0 以上なら壁率比の確認は不要（告示1352号ただし書き）
        return 1.0, ratios
    return min(values) / max(values), ratios


def evaluate(
    building: Building,
    roof_weight: str = "軽い",
    table: SeismicTable = TABLE_LEGACY,
    exterior_magnification: float = 2.5,
    interior_magnification: float = 1.0,
    wind_coefficient: float = WIND_COEFFICIENT,
) -> WallQuantityReport:
    """壁量計算を実行する。"""
    storeys = len(building.floors)
    results: List[FloorResult] = []

    for floor in building.floors:
        coefficient = table.coefficient(storeys, floor.storey, roof_weight)
        directions: List[DirectionResult] = []
        floor_walls: List[WallLine] = []

        for axis in ("X", "Y"):
            walls = wall_lines(floor, axis, exterior_magnification, interior_magnification)
            floor_walls.extend(walls)
            existing = sum(wall.effective_cm for wall in walls)
            # 風圧力は、その方向の力を受ける面（直交する面）の見付面積で決まる
            elevation = elevation_area_above(building, floor.storey, "Y" if axis == "X" else "X")
            quarter_ratio, quarter_detail = quarter_check(floor, axis, walls, coefficient)
            directions.append(
                DirectionResult(
                    axis=axis,
                    required_seismic_cm=floor.area_m2 * coefficient,
                    required_wind_cm=elevation * wind_coefficient,
                    existing_cm=existing,
                    elevation_area_m2=elevation,
                    quarter_ratio=quarter_ratio,
                    quarter_detail=quarter_detail,
                )
            )
        results.append(
            FloorResult(
                storey=floor.storey,
                floor_area_m2=floor.area_m2,
                directions=directions,
                walls=floor_walls,
            )
        )

    return WallQuantityReport(
        floors=results,
        roof_weight=roof_weight,
        table=table,
        magnifications={"外壁": exterior_magnification, "間仕切壁": interior_magnification},
    )


def to_markdown(report: WallQuantityReport) -> str:
    """壁量計算書（概算）の Markdown。"""
    axis_label = {"X": "桁行方向（東西）", "Y": "張り間方向（南北）"}
    lines = [
        "# 壁量計算書（概算）",
        "",
        f"- 屋根の重さ: {report.roof_weight}屋根",
        f"- 係数表: {report.table.name}（適用: {report.table.effective}）",
        f"- 壁倍率の仮定: 外壁 {report.magnifications['外壁']} / 間仕切壁 {report.magnifications['間仕切壁']}",
        f"- 壁量: **{'充足' if report.quantity_ok else '不足'}**（最小充足率 {report.worst_ratio:.2f}）",
        f"- 配置バランス（四分割法）: **{'適合' if report.balance_ok else '不適合'}**"
        + (f"（最小壁率比 {report.worst_balance:.2f}）" if report.worst_balance is not None else ""),
        "",
    ]
    if not report.balance_ok:
        lines += [
            "> **配置バランスが不適合**: 必要壁量は満たしていても、耐力壁が一方に偏っています。",
            "> 充足率の低い側端部分に耐力壁を追加するか、壁の仕様（壁倍率）を上げてください。",
            "",
        ]
    if not report.verified:
        lines += [
            "> **要確認**: 係数表が最新の告示値であることを確認していません。",
            f"> {report.table.note}",
            "",
        ]

    for floor in report.floors:
        lines += [
            f"## {floor.storey}階（床面積 {floor.floor_area_m2:.2f} m²）",
            "",
            "| 方向 | 必要壁量(地震) | 見付面積 | 必要壁量(風) | 採用 | 存在壁量 | 充足率 | 壁率比 | 判定 |",
            "| --- | ---: | ---: | ---: | :-: | ---: | ---: | ---: | :-: |",
        ]
        for direction in floor.directions:
            verdict = "○" if direction.ok and direction.balance_ok else "×"
            balance = (
                f"{direction.quarter_ratio:.2f}" if direction.quarter_ratio is not None else "—"
            )
            lines.append(
                f"| {axis_label[direction.axis]} | {direction.required_seismic_cm:.0f} cm | "
                f"{direction.elevation_area_m2:.2f} m² | {direction.required_wind_cm:.0f} cm | "
                f"{direction.governing} | {direction.existing_cm:.0f} cm | "
                f"{direction.ratio:.2f} | {balance} | {verdict} |"
            )
        lines.append("")
        lines += ["| 壁 | 方向 | 位置 | 実長 | 倍率 | 換算長 |", "| --- | :-: | ---: | ---: | ---: | ---: |"]
        for wall in floor.walls:
            lines.append(
                f"| {wall.kind} | {wall.axis} | {wall.position:.2f} m | {wall.length_m:.2f} m | "
                f"{wall.magnification} | {wall.effective_cm:.0f} cm |"
            )
        lines.append("")

    lines += [
        "---",
        "",
        "本計算は外周壁と一定長さ以上の間仕切壁を耐力壁と仮定した概算です。",
        "実際の耐力壁の位置・仕様（筋かい・面材・釘打ち）は伏図で確定し、",
        "接合部（N値計算）・横架材・基礎の検討とあわせて建築士が確認してください。",
        "",
    ]
    return "\n".join(lines)
