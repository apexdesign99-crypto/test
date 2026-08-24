"""外部データソースのアダプタ層（不動産 API / GIS・地図）。

実 API に依存しないよう、Protocol とローカル実装を分離している。
本番 API を使う場合は同じ Protocol を実装したクラスを差し替える。
"""

from .realestate import (
    Listing,
    RealEstateProvider,
    LocalRealEstateProvider,
)
from .gis import GisFeature, GisProvider, LocalGisProvider

__all__ = [
    "Listing",
    "RealEstateProvider",
    "LocalRealEstateProvider",
    "GisFeature",
    "GisProvider",
    "LocalGisProvider",
]
