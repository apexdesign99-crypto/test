"""外部データソースのアダプタ層（不動産 API / GIS・地図）。

実 API に依存しないよう、Protocol とローカル実装を分離している。
本番 API を使う場合は同じ Protocol を実装したクラスを差し替える。
"""

from .gis import GisFeature, GisProvider, LocalGisProvider, site_from_dict
from .geocoding import (
    CachedGeocoder,
    GeoPoint,
    Geocoder,
    GsiGeocoder,
    LocalFrame,
    LocalGeocoder,
)
from .hazard_lookup import HazardTileProvider, HttpTileSource, LocalTileSource
from .http import ApiError, NetworkUnavailable
from .parcel import LocalOverpassProvider, OverpassProvider, RoadCandidate
from .realestate import Listing, LocalRealEstateProvider, RealEstateProvider
from .resolve import ResolvedSite, SiteResolver, SourceRecord, build_resolver
from .zoning_lookup import (
    GeoJsonZoningProvider,
    ReinfolibZoningProvider,
    ZoningProvider,
    ZoningRecord,
)

__all__ = [
    "ApiError",
    "CachedGeocoder",
    "GeoJsonZoningProvider",
    "GeoPoint",
    "Geocoder",
    "GisFeature",
    "GisProvider",
    "GsiGeocoder",
    "HazardTileProvider",
    "HttpTileSource",
    "Listing",
    "LocalFrame",
    "LocalGeocoder",
    "LocalGisProvider",
    "LocalOverpassProvider",
    "LocalRealEstateProvider",
    "LocalTileSource",
    "NetworkUnavailable",
    "OverpassProvider",
    "RealEstateProvider",
    "ReinfolibZoningProvider",
    "ResolvedSite",
    "RoadCandidate",
    "SiteResolver",
    "SourceRecord",
    "ZoningProvider",
    "ZoningRecord",
    "build_resolver",
    "site_from_dict",
]
