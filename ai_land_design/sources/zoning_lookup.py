"""用途地域の取得（緯度経度 → 用途地域・建蔽率・容積率）。

2 つの実装を用意する。

`GeoJsonZoningProvider`
    国土数値情報「用途地域データ（A29）」の GeoJSON をローカルに置いて点内包判定する。
    オフラインで動き、精度も原典どおり。事前に対象都道府県のデータを取得しておく。
    https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-A29.html

`ReinfolibZoningProvider`
    国土交通省「不動産情報ライブラリ」の用途地域 API（XKT013）をタイル単位で叩く。
    利用には API キー（Ocp-Apim-Subscription-Key）が必要。
    https://www.reinfolib.mlit.go.jp/
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..geometry import Point
from ..models import UseDistrict
from .http import fetch
from .tiles import lonlat_to_tile

#: 国土数値情報 用途地域コード → 用途地域
LAND_USE_CODES: Dict[int, UseDistrict] = {
    1: UseDistrict.LOW_RISE_1,
    2: UseDistrict.LOW_RISE_2,
    3: UseDistrict.MID_HIGH_1,
    4: UseDistrict.MID_HIGH_2,
    5: UseDistrict.RESIDENTIAL_1,
    6: UseDistrict.RESIDENTIAL_2,
    7: UseDistrict.QUASI_RESIDENTIAL,
    8: UseDistrict.NEIGHBORHOOD_COMMERCIAL,
    9: UseDistrict.COMMERCIAL,
    10: UseDistrict.QUASI_INDUSTRIAL,
    11: UseDistrict.INDUSTRIAL,
    12: UseDistrict.EXCLUSIVE_INDUSTRIAL,
    21: UseDistrict.RURAL_RESIDENTIAL,
}

#: 用途地域コードの候補となる属性名（データ提供元により異なる）
_CODE_KEYS = ("A29_004", "youto_id", "用途地域コード", "yoto_code", "youto")
_NAME_KEYS = ("A29_005", "用途地域", "youto_name", "yoto", "name")
_BCR_KEYS = ("A29_006", "kenpei", "建蔽率", "kenpeiritsu", "buildingCoverageRatio")
_FAR_KEYS = ("A29_007", "yoseki", "容積率", "yosekiritsu", "floorAreaRatio")


@dataclass
class ZoningRecord:
    """取得できた用途地域の情報。"""

    use_district: UseDistrict
    building_coverage_ratio: Optional[float] = None  # 0-1
    floor_area_ratio: Optional[float] = None  # 1.0 = 100%
    source: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_district": self.use_district.value,
            "building_coverage_ratio": self.building_coverage_ratio,
            "floor_area_ratio": self.floor_area_ratio,
            "source": self.source,
        }


class ZoningProvider(Protocol):
    def zoning_at(self, lat: float, lon: float) -> Optional[ZoningRecord]:
        ...


def _first(properties: Dict[str, Any], keys: Sequence[str]) -> Optional[Any]:
    for key in keys:
        if key in properties and properties[key] not in ("", None):
            return properties[key]
    return None


def _as_ratio(value: Any, percent_scale: float) -> Optional[float]:
    """建蔽率・容積率を数値に正規化する（60 → 0.6 / 200 → 2.0）。"""
    if value is None:
        return None
    try:
        number = float(str(value).replace("%", "").strip())
    except ValueError:
        return None
    if number <= 0:
        return None
    return number / percent_scale if number > 1.5 else number


def parse_zoning_properties(properties: Dict[str, Any], source: str) -> Optional[ZoningRecord]:
    """GeoJSON の属性から用途地域を読み取る。提供元ごとの属性名の揺れを吸収する。"""
    use_district: Optional[UseDistrict] = None

    code = _first(properties, _CODE_KEYS)
    if code is not None:
        try:
            use_district = LAND_USE_CODES.get(int(str(code).strip()))
        except ValueError:
            use_district = None

    if use_district is None:
        name = _first(properties, _NAME_KEYS)
        if name is not None:
            text = str(name).strip()
            use_district = next((u for u in UseDistrict if u.value == text), None)

    if use_district is None:
        return None

    return ZoningRecord(
        use_district=use_district,
        building_coverage_ratio=_as_ratio(_first(properties, _BCR_KEYS), 100.0),
        floor_area_ratio=_as_ratio(_first(properties, _FAR_KEYS), 100.0),
        source=source,
        raw=dict(properties),
    )


def _point_in_ring(point: Point, ring: Sequence[Sequence[float]]) -> bool:
    x, y = point
    inside = False
    count = len(ring)
    for i in range(count):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % count][0], ring[(i + 1) % count][1]
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return inside


def point_in_geometry(lon: float, lat: float, geometry: Dict[str, Any]) -> bool:
    """GeoJSON の Polygon / MultiPolygon に点が含まれるか（穴も考慮）。"""
    kind = geometry.get("type")
    if kind == "Polygon":
        polygons = [geometry["coordinates"]]
    elif kind == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        return False

    for polygon in polygons:
        if not polygon:
            continue
        if _point_in_ring((lon, lat), polygon[0]) and not any(
            _point_in_ring((lon, lat), hole) for hole in polygon[1:]
        ):
            return True
    return False


def find_feature(lat: float, lon: float, features: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """点を含む最初のフィーチャを返す。"""
    for feature in features:
        geometry = feature.get("geometry")
        if geometry and point_in_geometry(lon, lat, geometry):
            return feature
    return None


class GeoJsonZoningProvider:
    """国土数値情報 A29（用途地域）の GeoJSON を読むオフライン実装。"""

    def __init__(self, path: str | Path, source: str = "国土数値情報 用途地域(A29)"):
        self.path = Path(path)
        self.source = source
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.features: List[Dict[str, Any]] = data.get("features", [])

    def zoning_at(self, lat: float, lon: float) -> Optional[ZoningRecord]:
        feature = find_feature(lat, lon, self.features)
        if feature is None:
            return None
        return parse_zoning_properties(feature.get("properties", {}), self.source)


#: 不動産情報ライブラリの API ベース URL
REINFOLIB_BASE = "https://www.reinfolib.mlit.go.jp/ex-api/external"
#: 用途地域 API の名称（提供元が変更した場合は設定で差し替える）
DEFAULT_ZONING_API = "XKT013"


class ReinfolibZoningProvider:
    """不動産情報ライブラリ（国土交通省）の用途地域 API。

    API キーは引数か環境変数 `REINFOLIB_API_KEY` から取得する。
    API 名（既定 XKT013）は設定で差し替えられるようにしてある。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        zoom: int = 15,
        api_name: str = DEFAULT_ZONING_API,
        base_url: str = REINFOLIB_BASE,
        timeout: float = 20.0,
        retries: int = 2,
    ):
        self.api_key = api_key or os.environ.get("REINFOLIB_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "API キーが必要です。REINFOLIB_API_KEY を設定するか api_key を渡してください。"
            )
        if not 11 <= zoom <= 15:
            raise ValueError("zoom は 11〜15 の範囲で指定してください")
        self.zoom = zoom
        self.api_name = api_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/{self.api_name}"

    @property
    def source(self) -> str:
        return f"不動産情報ライブラリ 用途地域API({self.api_name})"

    def zoning_at(self, lat: float, lon: float) -> Optional[ZoningRecord]:
        tile = lonlat_to_tile(lat, lon, self.zoom)
        response = fetch(
            self.endpoint,
            params={"response_format": "geojson", "z": tile.z, "x": tile.x, "y": tile.y},
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            timeout=self.timeout,
            retries=self.retries,
        )
        features = response.json().get("features", [])
        feature = find_feature(lat, lon, features)
        if feature is None:
            return None
        return parse_zoning_properties(feature.get("properties", {}), self.source)
