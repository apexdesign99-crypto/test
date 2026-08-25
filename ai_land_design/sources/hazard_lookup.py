"""ハザード情報の取得（ハザードマップポータルのラスタタイル）。

国土地理院「重ねるハザードマップ」が配信するタイル画像から、対象地点の
画素の色を読み取り、凡例に照らして浸水深・土砂災害警戒区域を判定する。

    洪水浸水想定区域（想定最大規模）  01_flood_l2_shinsuishin_data
    津波浸水想定                      04_tsunami_newlegend_data
    土砂災害警戒区域（土石流）        05_dosekiryukeikaikuiki
    土砂災害警戒区域（急傾斜地）      05_kyukeishakeikaikuiki

色から深さを読むため、凡例に一致しない配色が使われている地域では判定できない。
判定結果は目安であり、正式にはハザードマップ（自治体公表）で確認すること。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple

from ..models import Hazard
from .http import ApiError, NetworkUnavailable, fetch
from .tiles import RGBA, lonlat_to_tile, pixel_for

TILE_BASE = "https://disaportaldata.gsi.go.jp/raster"

FLOOD_L2 = "01_flood_l2_shinsuishin_data"
TSUNAMI = "04_tsunami_newlegend_data"
DEBRIS_FLOW = "05_dosekiryukeikaikuiki"
STEEP_SLOPE = "05_kyukeishakeikaikuiki"

#: 浸水深の凡例（RGB → 代表深さ [m]）。想定最大規模の配色。
FLOOD_LEGEND: List[Tuple[Tuple[int, int, int], float]] = [
    ((247, 245, 169), 0.3),   # 0.3m 未満
    ((255, 216, 192), 0.5),   # 0.3〜0.5m
    ((255, 183, 183), 3.0),   # 0.5〜3.0m
    ((255, 145, 145), 5.0),   # 3.0〜5.0m
    ((242, 133, 201), 10.0),  # 5.0〜10.0m
    ((220, 122, 220), 20.0),  # 10.0〜20.0m
    ((180, 130, 220), 20.0),  # 20.0m 以上
]

#: 凡例照合の許容色差（各チャンネルの絶対差の合計）
COLOR_TOLERANCE = 40


@dataclass
class HazardTileResult:
    """1 レイヤ分の判定結果。"""

    layer: str
    hit: bool
    value: Optional[float] = None
    color: Optional[RGBA] = None
    note: str = ""


def match_legend(color: RGBA, legend: List[Tuple[Tuple[int, int, int], float]]) -> Optional[float]:
    """画素の色を凡例に照合して代表値を返す。透明画素は該当なし。"""
    r, g, b, a = color
    if a < 32:
        return None
    best_value: Optional[float] = None
    best_distance = COLOR_TOLERANCE + 1
    for (lr, lg, lb), value in legend:
        distance = abs(r - lr) + abs(g - lg) + abs(b - lb)
        if distance < best_distance:
            best_distance, best_value = distance, value
    return best_value if best_distance <= COLOR_TOLERANCE else None


class TileSource(Protocol):
    def tile(self, layer: str, z: int, x: int, y: int) -> Optional[bytes]:
        ...


class HttpTileSource:
    """ハザードマップポータルからタイルを取得する。"""

    def __init__(self, base_url: str = TILE_BASE, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def tile(self, layer: str, z: int, x: int, y: int) -> Optional[bytes]:
        url = f"{self.base_url}/{layer}/{z}/{x}/{y}.png"
        try:
            return fetch(url, timeout=self.timeout, retries=1).body
        except ApiError as error:
            if error.status == 404:
                return None  # そのレイヤの対象外区域
            raise


class LocalTileSource:
    """ローカルのタイル画像を使うオフライン実装（テスト・キャッシュ用）。

    `{layer: {(z, x, y): bytes}}` を渡す。
    """

    def __init__(self, tiles: Dict[str, Dict[Tuple[int, int, int], bytes]]):
        self.tiles = tiles

    def tile(self, layer: str, z: int, x: int, y: int) -> Optional[bytes]:
        return self.tiles.get(layer, {}).get((z, x, y))


class HazardTileProvider:
    """タイル画像からハザード情報を判定する。"""

    source = "ハザードマップポータル（重ねるハザードマップ）"

    def __init__(self, tile_source: Optional[TileSource] = None, zoom: int = 16):
        self.tile_source = tile_source or HttpTileSource()
        self.zoom = zoom

    def _sample(self, layer: str, lat: float, lon: float) -> HazardTileResult:
        coord = lonlat_to_tile(lat, lon, self.zoom)
        try:
            data = self.tile_source.tile(layer, coord.z, coord.x, coord.y)
        except NetworkUnavailable as error:
            return HazardTileResult(layer, False, note=f"取得できず: {error}")
        if not data:
            return HazardTileResult(layer, False, note="対象区域外（タイルなし）")
        color = pixel_for(data, coord)
        if color[3] < 32:
            return HazardTileResult(layer, False, color=color, note="該当なし")
        return HazardTileResult(layer, True, color=color)

    def flood_depth(self, lat: float, lon: float) -> HazardTileResult:
        """洪水浸水想定区域（想定最大規模）の浸水深 [m]。"""
        result = self._sample(FLOOD_L2, lat, lon)
        if result.hit and result.color:
            depth = match_legend(result.color, FLOOD_LEGEND)
            if depth is None:
                result.note = f"凡例に一致しない色 {result.color[:3]}。手動確認が必要"
                result.hit = False
            else:
                result.value = depth
        return result

    def landslide(self, lat: float, lon: float) -> HazardTileResult:
        """土砂災害警戒区域（土石流・急傾斜地のいずれか）。"""
        for layer in (DEBRIS_FLOW, STEEP_SLOPE):
            result = self._sample(layer, lat, lon)
            if result.hit:
                return result
        return HazardTileResult(DEBRIS_FLOW, False, note="該当なし")

    def hazard_at(self, lat: float, lon: float) -> Tuple[Hazard, List[HazardTileResult]]:
        """`Hazard` モデルと、判定に使った各レイヤの結果を返す。"""
        flood = self.flood_depth(lat, lon)
        landslide = self.landslide(lat, lon)
        tsunami = self._sample(TSUNAMI, lat, lon)
        hazard = Hazard(
            flood_depth_m=flood.value or 0.0,
            landslide_risk=landslide.hit,
            liquefaction_risk=False,  # 液状化は全国一律のタイル配信がないため対象外
            quake_intensity_rank=3,
        )
        return hazard, [flood, landslide, tsunami]
