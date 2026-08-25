"""敷地形状・前面道路の参考情報（OpenStreetMap / Overpass API）。

日本の地番界（筆界）は OSM には入っていないため、**敷地境界そのものは取得できない**。
ここで取れるのは次の 2 つで、いずれも参考値として扱う。

    既存建物の外形   建て替えの検討や、敷地の向きを掴む手がかりになる
    前面道路の候補   幅員タグ（width / est_width）がある場合のみ幅員が分かる

正式な敷地形状は地積測量図・確定測量、道路幅員は道路台帳で確認すること。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..models import Direction
from .geocoding import LocalFrame
from .http import fetch

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"


@dataclass
class ParcelCandidate:
    """既存建物などの参考ポリゴン。"""

    kind: str  # "建物" など
    ring_lonlat: List[Tuple[float, float]]
    distance_m: float
    tags: Dict[str, str] = field(default_factory=dict)

    def polygon_xy(self, frame: LocalFrame) -> List[Tuple[float, float]]:
        return frame.polygon_to_xy(self.ring_lonlat)


@dataclass
class RoadCandidate:
    """前面道路の候補。"""

    name: str
    highway: str
    width_m: Optional[float]
    direction: Direction
    distance_m: float
    tags: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "highway": self.highway,
            "width_m": self.width_m,
            "direction": self.direction.value,
            "distance_m": round(self.distance_m, 1),
        }


def _distance_m(frame: LocalFrame, lat: float, lon: float) -> float:
    x, y = frame.to_xy(lat, lon)
    return math.hypot(x, y)


def _nearest_point_on_way(
    frame: LocalFrame, geometry: Sequence[Dict[str, float]]
) -> Tuple[float, float, float]:
    """線分列のうち基準点に最も近い点 (x, y, 距離)。

    頂点だけで測ると、長い道路では実際より遠く・見当違いの方位になるため、
    各線分への垂線の足も候補に入れる。
    """
    points = [frame.to_xy(p["lat"], p["lon"]) for p in geometry]
    best = (points[0][0], points[0][1], math.hypot(*points[0]))
    for index in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[index], points[index + 1]
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-12:
            candidates = [(x1, y1)]
        else:
            t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_sq))
            candidates = [(x1 + t * dx, y1 + t * dy)]
        for cx, cy in candidates:
            distance = math.hypot(cx, cy)
            if distance < best[2]:
                best = (cx, cy, distance)
    for x, y in points:
        distance = math.hypot(x, y)
        if distance < best[2]:
            best = (x, y, distance)
    return best


def _direction_of(x: float, y: float) -> Direction:
    """基準点から見た方位（4方位）。"""
    if abs(x) >= abs(y):
        return Direction.E if x > 0 else Direction.W
    return Direction.N if y > 0 else Direction.S


def _parse_width(tags: Dict[str, str]) -> Optional[float]:
    for key in ("width", "est_width", "maxwidth"):
        value = tags.get(key)
        if not value:
            continue
        try:
            return float(str(value).replace("m", "").strip())
        except ValueError:
            continue
    return None


class OverpassProvider:
    """Overpass API から周辺の建物・道路を取得する。"""

    source = "OpenStreetMap (Overpass API)"

    def __init__(self, endpoint: str = OVERPASS_ENDPOINT, timeout: float = 30.0):
        self.endpoint = endpoint
        self.timeout = timeout

    def _query(self, query: str) -> Dict[str, Any]:
        response = fetch(
            self.endpoint, params={"data": query}, timeout=self.timeout, retries=1
        )
        return response.json()

    def around(self, lat: float, lon: float, radius_m: int = 60) -> Dict[str, Any]:
        query = (
            f"[out:json][timeout:25];"
            f'(way(around:{radius_m},{lat},{lon})["building"];'
            f'way(around:{radius_m},{lat},{lon})["highway"];);'
            f"out geom tags;"
        )
        return self._query(query)

    def parse(
        self, data: Dict[str, Any], lat: float, lon: float
    ) -> Tuple[List[ParcelCandidate], List[RoadCandidate]]:
        frame = LocalFrame(lat, lon)
        buildings: List[ParcelCandidate] = []
        roads: List[RoadCandidate] = []

        for element in data.get("elements", []):
            geometry = element.get("geometry") or []
            if not geometry:
                continue
            tags = element.get("tags", {}) or {}
            near_x, near_y, nearest = _nearest_point_on_way(frame, geometry)

            if "building" in tags:
                ring = [(p["lon"], p["lat"]) for p in geometry]
                if ring[0] == ring[-1] and len(ring) > 3:
                    ring = ring[:-1]
                buildings.append(
                    ParcelCandidate("建物", ring, nearest, tags)
                )
            elif "highway" in tags:
                roads.append(
                    RoadCandidate(
                        name=tags.get("name", "（無名道路）"),
                        highway=tags.get("highway", ""),
                        width_m=_parse_width(tags),
                        direction=_direction_of(near_x, near_y),
                        distance_m=nearest,
                        tags=tags,
                    )
                )

        buildings.sort(key=lambda b: b.distance_m)
        roads.sort(key=lambda r: r.distance_m)
        return buildings, roads

    def lookup(
        self, lat: float, lon: float, radius_m: int = 60
    ) -> Tuple[List[ParcelCandidate], List[RoadCandidate]]:
        return self.parse(self.around(lat, lon, radius_m), lat, lon)


class LocalOverpassProvider(OverpassProvider):
    """Overpass の応答 JSON をローカルから読むオフライン実装。"""

    source = "OpenStreetMap（ローカル保存）"

    def __init__(self, data: Dict[str, Any]):
        super().__init__()
        self.data = data

    def around(self, lat: float, lon: float, radius_m: int = 60) -> Dict[str, Any]:
        return self.data
