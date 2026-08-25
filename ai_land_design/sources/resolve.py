"""住所から敷地条件を自動で組み立てる。

ジオコーディング → 用途地域 → ハザード → 前面道路 の順に公的データを引き、
`Site` を組み立てる。取得できた項目には出典を、できなかった項目には既定値と
警告を残すので、「どこまでが公的データで、どこからが仮定か」が常に分かる。

    住所 ─▶ 緯度経度（国土地理院）
             ├─▶ 用途地域・建蔽率・容積率（国土数値情報 / 不動産情報ライブラリ）
             ├─▶ 浸水深・土砂災害（ハザードマップポータル）
             └─▶ 前面道路の候補・既存建物（OpenStreetMap）

**敷地形状（筆界）は公開 API では取得できない**ため、敷地面積からの矩形近似を
既定とし、確定測量図の座標が手元にある場合はそれを渡す。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..geometry import Polygon, area as polygon_area
from ..models import (
    Direction,
    FireZone,
    Hazard,
    Road,
    Site,
    UseDistrict,
    Zoning,
)
from .geocoding import CachedGeocoder, GeoPoint, Geocoder, GsiGeocoder, LocalFrame, LocalGeocoder
from .hazard_lookup import HazardTileProvider
from .http import ApiError, NetworkUnavailable
from .parcel import OverpassProvider, ParcelCandidate, RoadCandidate
from .zoning_lookup import (
    GeoJsonZoningProvider,
    ReinfolibZoningProvider,
    ZoningProvider,
    ZoningRecord,
)

#: 敷地面積が分からない場合の既定値 [m2]（おおよそ50坪）
DEFAULT_SITE_AREA_M2 = 165.0
#: 矩形近似のときの 間口:奥行 比
DEFAULT_ASPECT = 0.8
#: 前面道路の幅員が分からない場合の既定値 [m]
DEFAULT_ROAD_WIDTH_M = 4.0


@dataclass
class SourceRecord:
    """1 項目分の出典。"""

    field: str
    value: str
    source: str
    fetched_at: str
    note: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class ResolvedSite:
    """住所から組み立てた敷地と、その根拠。"""

    site: Site
    geo: Optional[GeoPoint] = None
    provenance: List[SourceRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    road_candidates: List[RoadCandidate] = field(default_factory=list)
    building_candidates: List[ParcelCandidate] = field(default_factory=list)

    @property
    def confirmed_fields(self) -> List[str]:
        return [record.field for record in self.provenance]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site": self.site.to_dict(),
            "geo": self.geo.to_dict() if self.geo else None,
            "provenance": [r.to_dict() for r in self.provenance],
            "warnings": self.warnings,
            "road_candidates": [r.to_dict() for r in self.road_candidates],
            "building_candidates": [
                {"kind": b.kind, "distance_m": round(b.distance_m, 1), "points": len(b.ring_lonlat)}
                for b in self.building_candidates
            ],
        }


def rectangle_for(area_m2: float, frontage_direction: Direction, aspect: float = DEFAULT_ASPECT) -> Polygon:
    """面積と接道方位から、敷地の矩形近似をつくる。

    道路に面する辺を間口とし、間口:奥行 = `aspect` の矩形とする。
    """
    frontage = math.sqrt(area_m2 * aspect)
    depth = area_m2 / frontage
    if frontage_direction in (Direction.S, Direction.N):
        width, height = frontage, depth
    else:
        width, height = depth, frontage
    return [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]


class SiteResolver:
    """住所から `Site` を組み立てる。

    各プロバイダは差し替え可能で、`None` を渡した項目は取得を試みない。
    """

    def __init__(
        self,
        geocoder: Geocoder,
        zoning: Optional[ZoningProvider] = None,
        hazard: Optional[HazardTileProvider] = None,
        osm: Optional[OverpassProvider] = None,
    ):
        self.geocoder = geocoder
        self.zoning = zoning
        self.hazard = hazard
        self.osm = osm

    def resolve(
        self,
        address: str,
        area_m2: Optional[float] = None,
        polygon: Optional[Polygon] = None,
        road_width_m: Optional[float] = None,
        road_direction: Optional[Direction] = None,
        frontage_m: Optional[float] = None,
        land_price_jpy: Optional[int] = None,
        station_distance_m: Optional[int] = None,
        site_id: str = "resolved",
    ) -> ResolvedSite:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        provenance: List[SourceRecord] = []
        warnings: List[str] = []

        def record(field_name: str, value: Any, source: str, note: str = "") -> None:
            provenance.append(SourceRecord(field_name, str(value), source, now, note))

        # 1. 住所 → 緯度経度
        geo = self.geocoder.geocode(address)
        if geo is None:
            raise LookupError(f"住所を特定できませんでした: {address}")
        record("所在地", f"{geo.lat:.6f}, {geo.lon:.6f}", getattr(self.geocoder, "source", "不明"))

        # 2. 用途地域
        zoning_record: Optional[ZoningRecord] = None
        if self.zoning is not None:
            try:
                zoning_record = self.zoning.zoning_at(geo.lat, geo.lon)
            except (NetworkUnavailable, ApiError) as error:
                warnings.append(f"用途地域を取得できませんでした（{error}）。手入力してください。")
        if zoning_record is None:
            warnings.append(
                "用途地域を取得できなかったため、第一種住居地域・建蔽率60%・容積率200%を仮置きしています。"
                "都市計画情報で確認して修正してください。"
            )
            use_district = UseDistrict.RESIDENTIAL_1
            bcr, far = 0.6, 2.0
        else:
            use_district = zoning_record.use_district
            bcr = zoning_record.building_coverage_ratio or 0.6
            far = zoning_record.floor_area_ratio or 2.0
            record("用途地域", use_district.value, zoning_record.source)
            record("建蔽率", f"{bcr * 100:.0f}%", zoning_record.source)
            record("容積率", f"{far * 100:.0f}%", zoning_record.source)

        # 3. ハザード
        hazard = Hazard()
        if self.hazard is not None:
            try:
                hazard, results = self.hazard.hazard_at(geo.lat, geo.lon)
                for result in results:
                    if result.hit:
                        record(
                            f"ハザード（{result.layer}）",
                            result.value if result.value is not None else "該当",
                            self.hazard.source,
                            result.note,
                        )
                record("ハザード判定", "実施済み", self.hazard.source, "タイル配色からの判定（目安）")
            except (NetworkUnavailable, ApiError) as error:
                warnings.append(f"ハザード情報を取得できませんでした（{error}）。")
        else:
            warnings.append("ハザード情報は取得していません。")

        # 4. 前面道路・既存建物
        roads: List[RoadCandidate] = []
        buildings: List[ParcelCandidate] = []
        if self.osm is not None:
            try:
                buildings, roads = self.osm.lookup(geo.lat, geo.lon)
            except (NetworkUnavailable, ApiError) as error:
                warnings.append(f"周辺の道路・建物を取得できませんでした（{error}）。")

        vehicle_roads = [r for r in roads if r.highway not in ("footway", "path", "steps", "cycleway")]
        best_road = vehicle_roads[0] if vehicle_roads else None

        direction = road_direction or (best_road.direction if best_road else Direction.S)
        if road_width_m is not None:
            width = road_width_m
            record("前面道路 幅員", f"{width:.1f}m", "入力値")
        elif best_road and best_road.width_m:
            width = best_road.width_m
            record(
                "前面道路 幅員", f"{width:.1f}m", self.osm.source if self.osm else "OSM",
                f"{best_road.name}（{best_road.highway}）の width タグ",
            )
        else:
            width = DEFAULT_ROAD_WIDTH_M
            warnings.append(
                "前面道路の幅員を取得できなかったため 4.0m を仮置きしています。"
                "道路台帳・現地の実測で確認してください（幅員は容積率と斜線制限に直接効きます）。"
            )
        if best_road and road_direction is None:
            record(
                "前面道路 方位", direction.value, self.osm.source if self.osm else "OSM",
                f"{best_road.name} まで約{best_road.distance_m:.0f}m",
            )

        # 5. 敷地形状
        if polygon is not None:
            site_polygon = list(polygon)
            record("敷地形状", f"{polygon_area(site_polygon):.2f}m²", "入力値（測量図）")
        else:
            resolved_area = area_m2 or DEFAULT_SITE_AREA_M2
            if area_m2 is None:
                warnings.append(
                    f"敷地面積が未入力のため {DEFAULT_SITE_AREA_M2:.0f}m² を仮置きしています。"
                )
            else:
                record("敷地面積", f"{resolved_area:.2f}m²", "入力値")
            site_polygon = rectangle_for(resolved_area, direction)
            warnings.append(
                "敷地形状は面積からの矩形近似です。筆界は公開 API では取得できないため、"
                "地積測量図・確定測量図の座標で置き換えてください。"
            )

        frontage = frontage_m
        if frontage is None:
            xs = [p[0] for p in site_polygon]
            ys = [p[1] for p in site_polygon]
            frontage = (max(xs) - min(xs)) if direction in (Direction.S, Direction.N) else (
                max(ys) - min(ys)
            )
            warnings.append(
                f"接道長は敷地の間口（{frontage:.2f}m）で仮置きしています。実測値で確認してください。"
            )

        setback_road = width < 4.0
        zoning = Zoning(
            use_district=use_district,
            building_coverage_ratio=bcr,
            floor_area_ratio=far,
            fire_zone=FireZone.NONE,
            height_limit_m=10.0 if use_district.is_low_rise else None,
            wall_setback_m=1.0 if use_district.is_low_rise else 0.0,
        )
        warnings.append(
            "防火地域・日影規制・地区計画・高度地区は取得していません（すべて未指定として計算）。"
        )
        if use_district.is_low_rise:
            warnings.append(
                "低層住居専用地域のため、絶対高さ10m・外壁後退1.0mを仮置きしています。"
                "都市計画の指定値（10m/12m・1.0m/1.5m）を確認してください。"
            )

        site = Site(
            site_id=site_id,
            address=geo.address or address,
            polygon=site_polygon,
            zoning=zoning,
            roads=[
                Road(
                    width_m=width,
                    direction=direction,
                    frontage_m=frontage,
                    is_legal_road=True,
                    is_setback_road=setback_road,
                )
            ],
            land_price_jpy=land_price_jpy,
            station_distance_m=station_distance_m,
            hazard=hazard,
            lat=geo.lat,
            lon=geo.lon,
            note=f"{address} から自動取得",
            provenance=[r.to_dict() for r in provenance],
        )
        return ResolvedSite(
            site=site,
            geo=geo,
            provenance=provenance,
            warnings=warnings,
            road_candidates=roads,
            building_candidates=buildings,
        )


#: 環境変数でデータソースを切り替える
ENV_LIVE = "AI_LAND_DESIGN_LIVE"  # 1 なら外部 API を使う
ENV_ZONING_GEOJSON = "AI_LAND_DESIGN_ZONING_GEOJSON"  # 国土数値情報 A29 の GeoJSON
ENV_GEOCODE_CACHE = "AI_LAND_DESIGN_GEOCODE_CACHE"  # ジオコーディング結果のキャッシュ
ENV_GEOCODE_TABLE = "AI_LAND_DESIGN_GEOCODE_TABLE"  # 住所→座標のローカル辞書
ENV_REINFOLIB_KEY = "REINFOLIB_API_KEY"  # 不動産情報ライブラリ API キー


def build_resolver(
    live: Optional[bool] = None,
    zoning_geojson: Optional[str] = None,
    geocode_cache: Optional[str] = None,
    geocode_table: Optional[str] = None,
    reinfolib_key: Optional[str] = None,
) -> Tuple[SiteResolver, List[str]]:
    """設定・環境変数から `SiteResolver` を組み立てる。

    戻り値は (resolver, 使用するデータソースの説明)。`live` が偽の場合は
    外部 API を一切叩かず、ローカルのデータだけで解決する。
    """
    import os

    live = os.environ.get(ENV_LIVE, "") == "1" if live is None else live
    zoning_geojson = zoning_geojson or os.environ.get(ENV_ZONING_GEOJSON)
    geocode_cache = geocode_cache or os.environ.get(ENV_GEOCODE_CACHE)
    geocode_table = geocode_table or os.environ.get(ENV_GEOCODE_TABLE)
    reinfolib_key = reinfolib_key or os.environ.get(ENV_REINFOLIB_KEY)

    notes: List[str] = []

    geocoder: Geocoder
    if geocode_table:
        geocoder = LocalGeocoder(geocode_table)
        notes.append(f"ジオコーディング: ローカル辞書 {geocode_table}")
    elif live:
        geocoder = GsiGeocoder()
        notes.append("ジオコーディング: 国土地理院 住所検索API")
    else:
        raise ValueError(
            "住所を解決する手段がありません。"
            f"{ENV_LIVE}=1 で外部 API を使うか、{ENV_GEOCODE_TABLE} にローカル辞書を指定してください。"
        )
    if geocode_cache:
        geocoder = CachedGeocoder(geocoder, geocode_cache)
        notes.append(f"ジオコーディングのキャッシュ: {geocode_cache}")

    zoning: Optional[ZoningProvider] = None
    if zoning_geojson:
        zoning = GeoJsonZoningProvider(zoning_geojson)
        notes.append(f"用途地域: 国土数値情報 A29 {zoning_geojson}")
    elif live and reinfolib_key:
        zoning = ReinfolibZoningProvider(reinfolib_key)
        notes.append("用途地域: 不動産情報ライブラリ API")
    else:
        notes.append("用途地域: 取得手段なし（手入力）")

    hazard = None
    osm = None
    if live:
        hazard = HazardTileProvider()
        osm = OverpassProvider()
        notes.append("ハザード: ハザードマップポータル / 道路: OpenStreetMap")

    return SiteResolver(geocoder, zoning, hazard, osm), notes
