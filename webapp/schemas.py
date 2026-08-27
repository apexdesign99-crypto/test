"""リクエスト／レスポンスのスキーマ。

画面・API から受け取った入力を検証し、算定エンジンの `Site` / `Options` に変換する。
エンジン側のドメインモデルに Web の都合を持ち込まないよう、変換はここに閉じる。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from ai_land_design.application import ApplicationInfo, Party
from ai_land_design.listing import ListingInfo
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


class PartyIn(BaseModel):
    """申請に関わる者（建築主・設計者など）。未入力可。"""

    name: str = ""
    address: str = ""
    phone: str = ""
    qualification: str = ""
    registration: str = ""
    office: str = ""

    def to_party(self) -> Party:
        return Party(**self.model_dump())


class ApplicationIn(BaseModel):
    """確認申請書に記載する当事者・工程の情報。"""

    owner: PartyIn = Field(default_factory=PartyIn, description="建築主")
    agent: PartyIn = Field(default_factory=PartyIn, description="代理者")
    designer: PartyIn = Field(default_factory=PartyIn, description="設計者")
    supervisor: PartyIn = Field(default_factory=PartyIn, description="工事監理者")
    builder: PartyIn = Field(default_factory=PartyIn, description="工事施工者")
    application_date: Optional[str] = None
    start_date: str = ""
    completion_date: str = ""
    work_type: str = "新築"
    main_use: str = "一戸建ての住宅"

    def to_info(self) -> ApplicationInfo:
        return ApplicationInfo(
            owner=self.owner.to_party(),
            agent=self.agent.to_party(),
            designer=self.designer.to_party(),
            supervisor=self.supervisor.to_party(),
            builder=self.builder.to_party(),
            application_date=self.application_date,
            start_date=self.start_date,
            completion_date=self.completion_date,
            work_type=self.work_type,
            main_use=self.main_use,
        )


class ListingIn(BaseModel):
    """販売図面（マイソク）に載せる情報。"""

    property_name: str = Field(default="売地", max_length=80)
    price_jpy: Optional[int] = Field(default=None, ge=0)
    access: str = Field(default="", max_length=120)
    land_category: str = Field(default="宅地", max_length=40)
    city_planning: str = Field(default="市街化区域", max_length=40)
    current_state: str = Field(default="更地", max_length=40)
    delivery: str = Field(default="相談", max_length=40)
    transaction_type: str = Field(default="媒介", max_length=40)
    building_condition: str = Field(default="無", max_length=40)
    utilities: str = Field(default="公営水道・本下水・都市ガス", max_length=80)
    private_road_burden: str = Field(default="無", max_length=40)
    terrain: str = Field(default="平坦", max_length=40)
    note: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=80)
    license: str = Field(default="", max_length=80)

    def to_info(self) -> ListingInfo:
        return ListingInfo(**self.model_dump())


class OptionsIn(BaseModel):
    household_size: int = Field(default=4, ge=1, le=10)
    structure: str = Field(default=Structure.WOOD.value)
    grade: str = Field(default="標準")
    floor_height_m: float = Field(default=2.9, ge=2.2, le=5.0)
    ceiling_height_m: float = Field(default=2.4, ge=1.8, le=4.0)
    roof_weight: str = Field(default="軽い", description="壁量計算に使う屋根の重さ")
    seismic_table_verified: bool = Field(
        default=False, description="必要壁量の係数表が現行の告示値であることを確認済みか"
    )
    target_floor_area_m2: Optional[float] = Field(default=None, gt=0, le=3000)
    market_unit_price_per_tsubo: Optional[int] = Field(default=None, ge=0)

    @field_validator("structure")
    @classmethod
    def _check_structure(cls, value: str) -> str:
        if value not in {s.value for s in Structure}:
            raise ValueError(f"未知の構造: {value}")
        return value

    @field_validator("roof_weight")
    @classmethod
    def _check_roof_weight(cls, value: str) -> str:
        if value not in ("軽い", "重い"):
            raise ValueError("屋根の重さは「軽い」か「重い」で指定してください")
        return value

    @field_validator("grade")
    @classmethod
    def _check_grade(cls, value: str) -> str:
        from ai_land_design.cost import GRADE_FACTOR

        if value not in GRADE_FACTOR:
            raise ValueError(f"未知のグレード: {value}")
        return value

    def to_options(
        self,
        land_price_jpy: Optional[int],
        application: Optional[ApplicationInfo] = None,
        listing: Optional[ListingInfo] = None,
    ) -> Options:
        return Options(
            household_size=self.household_size,
            structure=next(s for s in Structure if s.value == self.structure),
            grade=self.grade,
            floor_height_m=self.floor_height_m,
            ceiling_height_m=self.ceiling_height_m,
            roof_weight=self.roof_weight,
            seismic_table_verified=self.seismic_table_verified,
            target_floor_area_m2=self.target_floor_area_m2,
            market_unit_price_per_tsubo=self.market_unit_price_per_tsubo,
            land_price_jpy=land_price_jpy,
            application=application or ApplicationInfo(),
            listing=listing or ListingInfo(),
        )


class SettingsIn(BaseModel):
    """データソース設定の更新。

    `reinfolib_api_key` は空文字なら「変更なし」として既存の値を保持する
    （画面はマスクした値しか持たないため）。
    """

    reinfolib_api_key: Optional[str] = Field(default=None, max_length=200)
    zoning_api: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]*$")
    live: Optional[bool] = None
    zoning_geojson: Optional[str] = Field(default=None, max_length=500)
    geocode_table: Optional[str] = Field(default=None, max_length=500)
    geocode_cache: Optional[str] = Field(default=None, max_length=500)


class ResolveRequest(BaseModel):
    """住所から敷地条件を自動取得するリクエスト。"""

    address: str = Field(min_length=1, max_length=200, description="所在地")
    area_m2: Optional[float] = Field(default=None, gt=0, le=100000, description="敷地面積")
    road_width_m: Optional[float] = Field(default=None, gt=0, le=50)
    frontage_m: Optional[float] = Field(default=None, gt=0, le=200)
    land_price_jpy: Optional[int] = Field(default=None, ge=0)
    station_distance_m: Optional[int] = Field(default=None, ge=0, le=20000)


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
    #: 自動取得した項目の出典（/api/resolve の結果をそのまま送り返すと報告書に載る）
    provenance: List[Dict[str, str]] = Field(default_factory=list)
    options: OptionsIn = Field(default_factory=OptionsIn)
    application: ApplicationIn = Field(default_factory=ApplicationIn)
    listing: ListingIn = Field(default_factory=ListingIn)

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
            "provenance": self.provenance,
        }

    def to_site(self):
        return site_from_dict(self.to_site_dict())

    def to_options(self) -> Options:
        return self.options.to_options(
            self.land_price_jpy, self.application.to_info(), self.listing.to_info()
        )
