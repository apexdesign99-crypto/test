"""テスト用の敷地ファクトリ。"""

from __future__ import annotations

from ai_land_design.models import (
    Direction,
    FireZone,
    Hazard,
    Road,
    Site,
    UseDistrict,
    Zoning,
)


def make_site(
    width: float = 14.0,
    depth: float = 16.0,
    use_district: UseDistrict = UseDistrict.RESIDENTIAL_1,
    bcr: float = 0.6,
    far: float = 2.0,
    road_width: float = 6.0,
    road_direction: Direction = Direction.S,
    frontage: float | None = None,
    **kwargs,
) -> Site:
    zoning = Zoning(
        use_district=use_district,
        building_coverage_ratio=bcr,
        floor_area_ratio=far,
        fire_zone=kwargs.pop("fire_zone", FireZone.NONE),
        height_limit_m=kwargs.pop("height_limit_m", None),
        wall_setback_m=kwargs.pop("wall_setback_m", 0.0),
        shadow_regulation=kwargs.pop("shadow_regulation", False),
        is_corner_lot=kwargs.pop("is_corner_lot", False),
    )
    road = Road(
        width_m=road_width,
        direction=road_direction,
        frontage_m=frontage if frontage is not None else width,
        is_legal_road=kwargs.pop("is_legal_road", True),
        is_setback_road=kwargs.pop("is_setback_road", False),
    )
    return Site(
        site_id=kwargs.pop("site_id", "test"),
        address=kwargs.pop("address", "東京都テスト区1-1-1"),
        polygon=[(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)],
        zoning=zoning,
        roads=[road] if kwargs.pop("with_road", True) else [],
        land_price_jpy=kwargs.pop("land_price_jpy", 90_000_000),
        station_distance_m=kwargs.pop("station_distance_m", 600),
        hazard=kwargs.pop("hazard", Hazard()),
    )
