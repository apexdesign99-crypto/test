"""リクエスト／レスポンスのスキーマ。

画面・API から受け取った入力を検証し、算定エンジンの `Site` / `Options` に変換する。
エンジン側のドメインモデルに Web の都合を持ち込まないよう、変換はここに閉じる。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from ai_land_design.models import Direction, FireZone, Structure, UseDistrict
from ai_land_design.pipeline import Options
from ai_land_design.sources.gis import site_from_dict


class ZoningIn(BaseModel):
    use_district: str = Field(default=UseDistrict.RESIDENTIAL_1.value, description="用途地域")
    building_coverage_ratio: float = Field(default=0.6, gt=0, le=1, description="建蔽率（0-1）")
    floor_area_ratio: float = Field(default=2.0, gt=0, le=15, description="容積率（1.0 = 100%）")
    fire_zone: str = Field(default=FireZone.NONE.value)
    height_limit_m: Optional[float] = Field(default=None, ge=0, le=100)
    wall_setback_m: float = Field(default=0.0, ge=0, le=10)
    shadow_regulation: bool = False
    is_corner_lot: bool = False
    scenic_district: bool = False

    @field_validator("use_district")
    @classmethod
    def _check_use_district(cls, value: str) -> str:
        if value not in {u.value for u in UseDistrict}:
            raise ValueError(f"未知の用途地域: {value}")
        return value

    @field_validator("fire_zone")
    @classmethod
    def _check_fire_zone(cls, value: str) -> str:
        if value not in {f.value for f in FireZone}:
            raise ValueError(f"未知の防火地域区分: {value}")
        return value


class RoadIn(BaseModel):
    width_m: float = Field(default=6.0, gt=0, le=50, description="前面道路の幅員")
    direction: str = Field(default=Direction.S.value, description="敷地から見た道路の方位")
    frontage_m: float = Field(default=10.0, gt=0, le=200, description="接道長")
    is_legal_road: bool = True
    is_setback_road: bool = Field(default=False, description="42条2項道路")

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, value: str) -> str:
        if value not in {d.value for d in Direction}:
            raise ValueError(f"未知の方位: {value}")
        return value


class HazardIn(BaseModel):
    flood_depth_m: float = Field(default=0.0, ge=0, le=20)
    landslide_risk: bool = False
    liquefaction_risk: bool = False
    quake_intensity_rank: int = Field(default=3, ge=1, le=5)


class OptionsIn(BaseModel):
    household_size: int = Field(default=4, ge=1, le=10)
    structure: str = Field(default=Structure.WOOD.value)
    grade: str = Field(default="標準")
    floor_height_m: float = Field(default=2.9, ge=2.2, le=5.0)
    target_floor_area_m2: Optional[float] = Field(default=None, gt=0, le=3000)
    market_unit_price_per_tsubo: Optional[int] = Field(default=None, ge=0)

    @field_validator("structure")
    @classmethod
    def _check_structure(cls, value: str) -> str:
        if value not in {s.value for s in Structure}:
            raise ValueError(f"未知の構造: {value}")
        return value

    @field_validator("grade")
    @classmethod
    def _check_grade(cls, value: str) -> str:
        from ai_land_design.cost import GRADE_FACTOR

        if value not in GRADE_FACTOR:
            raise ValueError(f"未知のグレード: {value}")
        return value

    def to_options(self, land_price_jpy: Optional[int]) -> Options:
        return Options(
            household_size=self.household_size,
            structure=next(s for s in Structure if s.value == self.structure),
            grade=self.grade,
            floor_height_m=self.floor_height_m,
            target_floor_area_m2=self.target_floor_area_m2,
            market_unit_price_per_tsubo=self.market_unit_price_per_tsubo,
            land_price_jpy=land_price_jpy,
        )


class AnalyzeRequest(BaseModel):
    """診断リクエスト。

    敷地形状は `polygon`（頂点座標）か、`width_m` / `depth_m`（間口×奥行の矩形）の
    どちらかで指定する。両方省略された場合はエラー。
    """

    site_id: str = "custom"
    address: str = ""
    polygon: Optional[List[Tuple[float, float]]] = None
    width_m: Optional[float] = Field(default=None, gt=0, le=500)
    depth_m: Optional[float] = Field(default=None, gt=0, le=500)
    zoning: ZoningIn = Field(default_factory=ZoningIn)
    roads: List[RoadIn] = Field(default_factory=lambda: [RoadIn()])
    hazard: HazardIn = Field(default_factory=HazardIn)
    land_price_jpy: Optional[int] = Field(default=None, ge=0)
    station_distance_m: Optional[int] = Field(default=None, ge=0, le=20000)
    note: str = ""
    options: OptionsIn = Field(default_factory=OptionsIn)

    @field_validator("polygon")
    @classmethod
    def _check_polygon(cls, value):
        if value is not None and len(value) < 3:
            raise ValueError("敷地ポリゴンには3点以上が必要です")
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> "AnalyzeRequest":
        if self.polygon is None and not (self.width_m and self.depth_m):
            raise ValueError("polygon か width_m / depth_m のいずれかを指定してください")
        return self

    def resolved_polygon(self) -> List[Tuple[float, float]]:
        if self.polygon:
            return [(float(x), float(y)) for x, y in self.polygon]
        width, depth = float(self.width_m or 0), float(self.depth_m or 0)
        return [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]

    def to_site_dict(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id or "custom",
            "address": self.address,
            "polygon": [list(p) for p in self.resolved_polygon()],
            "zoning": self.zoning.model_dump(),
            "roads": [r.model_dump() for r in self.roads],
            "hazard": self.hazard.model_dump(),
            "land_price_jpy": self.land_price_jpy,
            "station_distance_m": self.station_distance_m,
            "note": self.note,
        }

    def to_site(self):
        return site_from_dict(self.to_site_dict())

    def to_options(self) -> Options:
        return self.options.to_options(self.land_price_jpy)
