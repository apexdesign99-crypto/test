"""ドメインモデル（敷地・法規条件・診断結果など）。

すべて標準ライブラリの dataclass で表現し、`to_dict()` で JSON 化できる。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geometry import Point, Polygon, area, shape_regularity


class UseDistrict(str, Enum):
    """用途地域（都市計画法 第8条）。"""

    LOW_RISE_1 = "第一種低層住居専用地域"
    LOW_RISE_2 = "第二種低層住居専用地域"
    MID_HIGH_1 = "第一種中高層住居専用地域"
    MID_HIGH_2 = "第二種中高層住居専用地域"
    RESIDENTIAL_1 = "第一種住居地域"
    RESIDENTIAL_2 = "第二種住居地域"
    QUASI_RESIDENTIAL = "準住居地域"
    RURAL_RESIDENTIAL = "田園住居地域"
    NEIGHBORHOOD_COMMERCIAL = "近隣商業地域"
    COMMERCIAL = "商業地域"
    QUASI_INDUSTRIAL = "準工業地域"
    INDUSTRIAL = "工業地域"
    EXCLUSIVE_INDUSTRIAL = "工業専用地域"

    @property
    def is_low_rise(self) -> bool:
        """低層住居専用系（絶対高さ制限・外壁後退・北側斜線の対象）。"""
        return self in (
            UseDistrict.LOW_RISE_1,
            UseDistrict.LOW_RISE_2,
            UseDistrict.RURAL_RESIDENTIAL,
        )

    @property
    def is_mid_high(self) -> bool:
        """中高層住居専用系（北側斜線の対象）。"""
        return self in (UseDistrict.MID_HIGH_1, UseDistrict.MID_HIGH_2)

    @property
    def is_residential_group(self) -> bool:
        """住居系用途地域（前面道路容積率係数 0.4 / 道路斜線 1.25 の系統）。"""
        return self in (
            UseDistrict.LOW_RISE_1,
            UseDistrict.LOW_RISE_2,
            UseDistrict.MID_HIGH_1,
            UseDistrict.MID_HIGH_2,
            UseDistrict.RESIDENTIAL_1,
            UseDistrict.RESIDENTIAL_2,
            UseDistrict.QUASI_RESIDENTIAL,
            UseDistrict.RURAL_RESIDENTIAL,
        )

    @property
    def allows_dwelling(self) -> bool:
        """住宅の建築可否（工業専用地域は不可）。"""
        return self is not UseDistrict.EXCLUSIVE_INDUSTRIAL


class FireZone(str, Enum):
    NONE = "指定なし"
    QUASI = "準防火地域"
    FIRE = "防火地域"


class Structure(str, Enum):
    WOOD = "木造"
    STEEL = "鉄骨造"
    RC = "鉄筋コンクリート造"


class Direction(str, Enum):
    N = "北"
    E = "東"
    S = "南"
    W = "西"

    @property
    def azimuth(self) -> float:
        """真北を 0 度とする方位角 [deg]。"""
        return {"北": 0.0, "東": 90.0, "南": 180.0, "西": 270.0}[self.value]


@dataclass
class Road:
    """前面道路。"""

    width_m: float
    direction: Direction
    frontage_m: float
    #: 建築基準法上の道路（42条各項）であるか。False の場合は接道義務を満たさない。
    is_legal_road: bool = True
    #: 42条2項道路（幅員 4m 未満・セットバック要）
    is_setback_road: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        return d


@dataclass
class Zoning:
    """都市計画・建築基準法上の敷地条件。"""

    use_district: UseDistrict
    building_coverage_ratio: float  # 建蔽率（指定）0..1
    floor_area_ratio: float  # 容積率（指定）例: 2.0 = 200%
    fire_zone: FireZone = FireZone.NONE
    height_limit_m: Optional[float] = None  # 絶対高さ制限（低層系 10 または 12m）
    wall_setback_m: float = 0.0  # 外壁の後退距離（低層系 1.0 / 1.5m など）
    shadow_regulation: bool = False  # 日影規制の指定有無
    is_corner_lot: bool = False  # 角地（建蔽率 +10% 緩和の対象）
    scenic_district: bool = False  # 風致地区など追加規制

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["use_district"] = self.use_district.value
        d["fire_zone"] = self.fire_zone.value
        return d


@dataclass
class Hazard:
    """ハザード情報（GIS 由来）。"""

    flood_depth_m: float = 0.0
    landslide_risk: bool = False
    liquefaction_risk: bool = False
    quake_intensity_rank: int = 3  # 1(低) .. 5(高) の相対ランク

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Site:
    """診断対象の敷地。"""

    site_id: str
    address: str
    polygon: Polygon
    zoning: Zoning
    roads: List[Road] = field(default_factory=list)
    land_price_jpy: Optional[int] = None  # 売出価格または取得想定価格
    station_distance_m: Optional[int] = None
    hazard: Hazard = field(default_factory=Hazard)
    lat: Optional[float] = None
    lon: Optional[float] = None
    note: str = ""

    @property
    def area_m2(self) -> float:
        return area(self.polygon)

    @property
    def area_tsubo(self) -> float:
        return self.area_m2 / 3.305785

    @property
    def regularity(self) -> float:
        return shape_regularity(self.polygon)

    @property
    def widest_road(self) -> Optional[Road]:
        legal = [r for r in self.roads if r.is_legal_road]
        return max(legal, key=lambda r: r.width_m) if legal else None

    @property
    def unit_price_per_tsubo(self) -> Optional[int]:
        if self.land_price_jpy is None or self.area_tsubo <= 0:
            return None
        return int(round(self.land_price_jpy / self.area_tsubo))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "address": self.address,
            "polygon": [list(p) for p in self.polygon],
            "area_m2": round(self.area_m2, 2),
            "area_tsubo": round(self.area_tsubo, 2),
            "regularity": round(self.regularity, 3),
            "zoning": self.zoning.to_dict(),
            "roads": [r.to_dict() for r in self.roads],
            "land_price_jpy": self.land_price_jpy,
            "unit_price_per_tsubo": self.unit_price_per_tsubo,
            "station_distance_m": self.station_distance_m,
            "hazard": self.hazard.to_dict(),
            "lat": self.lat,
            "lon": self.lon,
            "note": self.note,
        }


@dataclass
class Finding:
    """診断・判定で得られた指摘事項。"""

    level: str  # "info" | "warn" | "block"
    code: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreItem:
    name: str
    score: float  # 0..100
    weight: float
    comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 1),
            "weight": self.weight,
            "comment": self.comment,
        }


@dataclass
class Diagnosis:
    """AI 土地診断の結果。"""

    total_score: float
    rank: str
    items: List[ScoreItem]
    findings: List[Finding]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_score": round(self.total_score, 1),
            "rank": self.rank,
            "items": [i.to_dict() for i in self.items],
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class HeightLimit:
    name: str
    limit_m: float
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "limit_m": round(self.limit_m, 2), "detail": self.detail}


@dataclass
class Envelope:
    """建築可能ボリューム（建築可能判定の結果）。"""

    buildable: bool
    effective_site_area_m2: float
    applied_coverage_ratio: float
    applied_far: float
    max_building_area_m2: float
    max_floor_area_m2: float
    max_height_m: float
    height_limits: List[HeightLimit]
    max_storeys: int
    findings: List[Finding]
    setback_loss_m2: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "buildable": self.buildable,
            "effective_site_area_m2": round(self.effective_site_area_m2, 2),
            "setback_loss_m2": round(self.setback_loss_m2, 2),
            "applied_coverage_ratio": round(self.applied_coverage_ratio, 3),
            "applied_far": round(self.applied_far, 3),
            "max_building_area_m2": round(self.max_building_area_m2, 2),
            "max_floor_area_m2": round(self.max_floor_area_m2, 2),
            "max_height_m": round(self.max_height_m, 2),
            "height_limits": [h.to_dict() for h in self.height_limits],
            "max_storeys": self.max_storeys,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class Room:
    name: str
    x: float
    y: float
    w: float
    h: float
    storey: int = 1

    @property
    def area_m2(self) -> float:
        return self.w * self.h

    @property
    def jo(self) -> float:
        """畳数（1 畳 = 1.62 m2 の中京間換算）。"""
        return self.area_m2 / 1.62

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "storey": self.storey,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "w": round(self.w, 3),
            "h": round(self.h, 3),
            "area_m2": round(self.area_m2, 2),
            "jo": round(self.jo, 1),
        }


@dataclass
class Floor:
    storey: int
    footprint: Polygon
    rooms: List[Room]
    height_m: float

    @property
    def area_m2(self) -> float:
        return area(self.footprint)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storey": self.storey,
            "height_m": self.height_m,
            "area_m2": round(self.area_m2, 2),
            "footprint": [list(p) for p in self.footprint],
            "rooms": [r.to_dict() for r in self.rooms],
        }


@dataclass
class Building:
    """AI 間取り + 3D 外観が確定した建物案。"""

    structure: Structure
    floors: List[Floor]
    total_floor_area_m2: float
    height_m: float
    ldk_type: str
    roof: str = "切妻"

    @property
    def storeys(self) -> int:
        return len(self.floors)

    @property
    def footprint_area_m2(self) -> float:
        return self.floors[0].area_m2 if self.floors else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "structure": self.structure.value,
            "ldk_type": self.ldk_type,
            "roof": self.roof,
            "storeys": self.storeys,
            "height_m": round(self.height_m, 2),
            "footprint_area_m2": round(self.footprint_area_m2, 2),
            "total_floor_area_m2": round(self.total_floor_area_m2, 2),
            "floors": [f.to_dict() for f in self.floors],
        }


@dataclass
class CostItem:
    name: str
    amount_jpy: int
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CostBreakdown:
    """建築費 / 総事業費。"""

    construction_items: List[CostItem]
    other_items: List[CostItem]
    land_price_jpy: int
    tax_rate: float = 0.10

    @property
    def construction_subtotal_jpy(self) -> int:
        return sum(i.amount_jpy for i in self.construction_items)

    @property
    def construction_tax_jpy(self) -> int:
        return int(round(self.construction_subtotal_jpy * self.tax_rate))

    @property
    def construction_total_jpy(self) -> int:
        return self.construction_subtotal_jpy + self.construction_tax_jpy

    @property
    def other_total_jpy(self) -> int:
        return sum(i.amount_jpy for i in self.other_items)

    @property
    def project_total_jpy(self) -> int:
        return self.land_price_jpy + self.construction_total_jpy + self.other_total_jpy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "construction_items": [i.to_dict() for i in self.construction_items],
            "construction_subtotal_jpy": self.construction_subtotal_jpy,
            "construction_tax_jpy": self.construction_tax_jpy,
            "construction_total_jpy": self.construction_total_jpy,
            "other_items": [i.to_dict() for i in self.other_items],
            "other_total_jpy": self.other_total_jpy,
            "land_price_jpy": self.land_price_jpy,
            "project_total_jpy": self.project_total_jpy,
        }
