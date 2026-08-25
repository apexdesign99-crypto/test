"""ジオコーディング（住所 → 緯度経度）と、緯度経度 → ローカル平面座標の変換。

既定は国土地理院の住所検索 API（利用登録不要）。取得結果はキャッシュでき、
ネットワークが使えない環境ではローカルの辞書を使う実装に差し替えられる。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .http import ApiError, NetworkUnavailable, fetch

#: 国土地理院 住所検索 API
GSI_ADDRESS_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch"
#: 国土地理院 逆ジオコーディング API
GSI_REVERSE = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"


@dataclass
class GeoPoint:
    """ジオコーディング結果。"""

    lat: float
    lon: float
    address: str = ""
    source: str = ""
    accuracy: Optional[int] = None  # 国土地理院の iLevel 相当（大きいほど詳細）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Geocoder(Protocol):
    def geocode(self, address: str) -> Optional[GeoPoint]:
        ...


class GsiGeocoder:
    """国土地理院 住所検索 API。

    応答は GeoJSON の FeatureCollection で、`geometry.coordinates` が [経度, 緯度]。
    """

    source = "国土地理院 住所検索API"

    def geocode(self, address: str) -> Optional[GeoPoint]:
        response = fetch(GSI_ADDRESS_SEARCH, params={"q": address})
        features = response.json()
        if not features:
            return None
        feature = features[0] if isinstance(features, list) else features.get("features", [None])[0]
        if not feature:
            return None
        lon, lat = feature["geometry"]["coordinates"][:2]
        properties = feature.get("properties", {})
        return GeoPoint(
            lat=float(lat),
            lon=float(lon),
            address=properties.get("title", address),
            source=self.source,
            accuracy=properties.get("iLevel") or properties.get("addressCode"),
        )

    def reverse(self, lat: float, lon: float) -> Optional[str]:
        """緯度経度 → 住所（市区町村＋町字）。"""
        try:
            data = fetch(GSI_REVERSE, params={"lat": lat, "lon": lon}).json()
        except (ApiError, NetworkUnavailable):
            return None
        results = data.get("results") or {}
        return results.get("lv01Nm")


class LocalGeocoder:
    """住所→座標の対応表（JSON）を読むオフライン実装。

    形式: `{"東京都世田谷区代田1-1-1": {"lat": 35.66, "lon": 139.65}}`
    """

    source = "ローカル辞書"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._table: Dict[str, Dict[str, float]] = json.loads(
            self.path.read_text(encoding="utf-8")
        )

    def geocode(self, address: str) -> Optional[GeoPoint]:
        record = self._table.get(address)
        if record is None:
            # 前方一致でも探す（番地違いを吸収する）
            for key, value in self._table.items():
                if address.startswith(key) or key.startswith(address):
                    record = value
                    break
        if record is None:
            return None
        return GeoPoint(
            lat=float(record["lat"]),
            lon=float(record["lon"]),
            address=record.get("address", address),
            source=self.source,
        )


class CachedGeocoder:
    """他のジオコーダの結果を JSON ファイルにキャッシュする。

    同じ住所を何度も問い合わせないため、また取得結果を後から再現できるようにするため。
    """

    def __init__(self, inner: Geocoder, cache_path: str | Path):
        self.inner = inner
        self.cache_path = Path(cache_path)
        self._cache: Dict[str, Any] = {}
        if self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    @property
    def source(self) -> str:
        return getattr(self.inner, "source", "不明") + "（キャッシュ）"

    def geocode(self, address: str) -> Optional[GeoPoint]:
        if address in self._cache:
            record = self._cache[address]
            return GeoPoint(**record) if record else None
        result = self.inner.geocode(address)
        self._cache[address] = result.to_dict() if result else None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result


#: 緯度1度あたりの距離 [m]（日本付近の平均）
METERS_PER_DEG_LAT = 110_946.0
#: 経度1度あたりの距離 [m]（緯度0度）
METERS_PER_DEG_LON = 111_319.5


class LocalFrame:
    """基準点まわりの局所平面直角座標（x 東・y 北、単位 m）。

    敷地程度（数十m）の範囲では接平面近似で十分な精度が出る。
    正式な図面用の座標が必要な場合は平面直角座標系（19系）への変換を行うこと。
    """

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        self._lon_scale = METERS_PER_DEG_LON * math.cos(math.radians(lat0))

    def to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        return ((lon - self.lon0) * self._lon_scale, (lat - self.lat0) * METERS_PER_DEG_LAT)

    def to_lonlat(self, x: float, y: float) -> Tuple[float, float]:
        return (self.lon0 + x / self._lon_scale, self.lat0 + y / METERS_PER_DEG_LAT)

    def polygon_to_xy(self, ring: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """[(経度, 緯度), ...] → [(x, y), ...]"""
        return [self.to_xy(lat, lon) for lon, lat in ring]
