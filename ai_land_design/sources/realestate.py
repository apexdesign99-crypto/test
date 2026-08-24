"""不動産 API アダプタ。

`RealEstateProvider` は「住所・条件から売地情報を引く」インタフェース。
同梱の `LocalRealEstateProvider` は JSON ファイルを読むだけのオフライン実装で、
テストとサンプル実行に使う。国土交通省「不動産情報ライブラリ」や各社 API を
使う場合は、同じシグネチャの `search()` / `get()` を実装したクラスを渡す。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class Listing:
    """売地情報 1 件。"""

    listing_id: str
    address: str
    price_jpy: int
    area_m2: float
    station_distance_m: Optional[int] = None
    source: str = "local"
    raw: Dict[str, Any] = None  # type: ignore[assignment]

    @property
    def unit_price_per_tsubo(self) -> int:
        tsubo = self.area_m2 / 3.305785
        return int(round(self.price_jpy / tsubo)) if tsubo > 0 else 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        d["unit_price_per_tsubo"] = self.unit_price_per_tsubo
        return d


class RealEstateProvider(Protocol):
    """不動産 API の最小インタフェース。"""

    def search(self, address: str, limit: int = 20) -> List[Listing]:
        ...

    def get(self, listing_id: str) -> Optional[Listing]:
        ...


class LocalRealEstateProvider:
    """JSON ファイルを読むオフライン実装。

    ファイル形式は `{"listings": [ {...}, ... ]}`。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._listings: List[Listing] = []
        self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._listings = [
            Listing(
                listing_id=str(r["listing_id"]),
                address=r["address"],
                price_jpy=int(r["price_jpy"]),
                area_m2=float(r["area_m2"]),
                station_distance_m=r.get("station_distance_m"),
                source=r.get("source", "local"),
                raw=r,
            )
            for r in data.get("listings", [])
        ]

    def search(self, address: str, limit: int = 20) -> List[Listing]:
        hits = [l for l in self._listings if address in l.address] if address else list(self._listings)
        return hits[:limit]

    def get(self, listing_id: str) -> Optional[Listing]:
        for l in self._listings:
            if l.listing_id == listing_id:
                return l
        return None

    def median_unit_price(self, address_prefix: str) -> Optional[int]:
        """周辺相場（坪単価の中央値）。市場性スコアの基準に使う。"""
        prices = sorted(
            l.unit_price_per_tsubo for l in self._listings if address_prefix in l.address
        )
        if not prices:
            return None
        mid = len(prices) // 2
        if len(prices) % 2:
            return prices[mid]
        return (prices[mid - 1] + prices[mid]) // 2
