"""GIS・地図アダプタ。

敷地形状（ポリゴン）、用途地域、前面道路、ハザード情報を返す。
`LocalGisProvider` は GeoJSON 風の JSON を読むオフライン実装。
都市計画 GIS / 国土数値情報 / 自治体 API に接続する場合は
同じ `feature_for()` を実装したクラスに差し替える。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from ..geometry import Polygon
from ..models import (
    Direction,
    FireZone,
    Hazard,
    Road,
    Site,
    UseDistrict,
    Zoning,
)


@dataclass
class GisFeature:
    """GIS から得た 1 筆分の素データ。"""

    site_id: str
    address: str
    polygon: Polygon
    properties: Dict[str, Any] = field(default_factory=dict)


class GisProvider(Protocol):
    def feature_for(self, key: str) -> Optional[GisFeature]:
        ...


def _direction(value: str) -> Direction:
    for d in Direction:
        if d.value == value or d.name == value.upper():
            return d
    raise ValueError(f"未知の方位: {value}")


def _use_district(value: str) -> UseDistrict:
    for u in UseDistrict:
        if u.value == value or u.name == value.upper():
            return u
    raise ValueError(f"未知の用途地域: {value}")


def _fire_zone(value: str) -> FireZone:
    for f in FireZone:
        if f.value == value or f.name == value.upper():
            return f
    raise ValueError(f"未知の防火地域区分: {value}")


def site_from_dict(data: Dict[str, Any]) -> Site:
    """JSON 辞書から `Site` を組み立てる（CLI の --input でも使用）。"""
    z = data["zoning"]
    zoning = Zoning(
        use_district=_use_district(z["use_district"]),
        building_coverage_ratio=float(z["building_coverage_ratio"]),
        floor_area_ratio=float(z["floor_area_ratio"]),
        fire_zone=_fire_zone(z.get("fire_zone", "指定なし")),
        height_limit_m=z.get("height_limit_m"),
        wall_setback_m=float(z.get("wall_setback_m", 0.0)),
        shadow_regulation=bool(z.get("shadow_regulation", False)),
        is_corner_lot=bool(z.get("is_corner_lot", False)),
        scenic_district=bool(z.get("scenic_district", False)),
    )
    roads = [
        Road(
            width_m=float(r["width_m"]),
            direction=_direction(r["direction"]),
            frontage_m=float(r["frontage_m"]),
            is_legal_road=bool(r.get("is_legal_road", True)),
            is_setback_road=bool(r.get("is_setback_road", False)),
        )
        for r in data.get("roads", [])
    ]
    h = data.get("hazard", {})
    hazard = Hazard(
        flood_depth_m=float(h.get("flood_depth_m", 0.0)),
        landslide_risk=bool(h.get("landslide_risk", False)),
        liquefaction_risk=bool(h.get("liquefaction_risk", False)),
        quake_intensity_rank=int(h.get("quake_intensity_rank", 3)),
    )
    return Site(
        site_id=str(data.get("site_id", "site")),
        address=data.get("address", ""),
        polygon=[tuple(p) for p in data["polygon"]],
        zoning=zoning,
        roads=roads,
        land_price_jpy=data.get("land_price_jpy"),
        station_distance_m=data.get("station_distance_m"),
        hazard=hazard,
        lat=data.get("lat"),
        lon=data.get("lon"),
        note=data.get("note", ""),
        provenance=list(data.get("provenance", [])),
    )


class LocalGisProvider:
    """JSON ファイル（`{"sites": [...]}`）を読むオフライン実装。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._raw: List[Dict[str, Any]] = json.loads(
            self.path.read_text(encoding="utf-8")
        ).get("sites", [])

    def feature_for(self, key: str) -> Optional[GisFeature]:
        for r in self._raw:
            if key in (r.get("site_id"), r.get("address")) or key in r.get("address", ""):
                return GisFeature(
                    site_id=r.get("site_id", "site"),
                    address=r.get("address", ""),
                    polygon=[tuple(p) for p in r["polygon"]],
                    properties=r,
                )
        return None

    def site_for(self, key: str) -> Optional[Site]:
        feature = self.feature_for(key)
        return site_from_dict(feature.properties) if feature else None

    def all_sites(self) -> List[Site]:
        return [site_from_dict(r) for r in self._raw]
