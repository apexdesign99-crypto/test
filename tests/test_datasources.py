"""公的データソース（ジオコーディング・用途地域・ハザード・OSM）のテスト。

外部 API は叩かず、`tests/fixtures/` に置いた応答で検証する。
実 API に対する疎通確認は `AI_LAND_DESIGN_LIVE_TEST=1` のときだけ実行する。
"""

import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from ai_land_design.models import Direction, UseDistrict
from ai_land_design.sources import hazard_lookup, tiles
from ai_land_design.sources.geocoding import (
    CachedGeocoder,
    GeoPoint,
    LocalFrame,
    LocalGeocoder,
)
from ai_land_design.sources.hazard_lookup import (
    FLOOD_L2,
    HazardTileProvider,
    LocalTileSource,
    match_legend,
)
from ai_land_design.sources.http import NetworkUnavailable, fetch
from ai_land_design.sources.parcel import LocalOverpassProvider
from ai_land_design.sources.resolve import SiteResolver, build_resolver, rectangle_for
from ai_land_design.sources.zoning_lookup import (
    GeoJsonZoningProvider,
    parse_zoning_properties,
    point_in_geometry,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIVE = os.environ.get("AI_LAND_DESIGN_LIVE_TEST") == "1"


def solid_png(color, size=32):
    raw = (b"\x00" + bytes(color) * size) * size

    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class TileMathTest(unittest.TestCase):
    def test_known_tile(self):
        coord = tiles.lonlat_to_tile(35.6595, 139.6535, 14)
        self.assertEqual((coord.z, coord.x, coord.y), (14, 14547, 6452))
        self.assertTrue(0 <= coord.px < 256 and 0 <= coord.py < 256)

    def test_zoom_doubles_tile_index(self):
        low = tiles.lonlat_to_tile(35.0, 139.0, 10)
        high = tiles.lonlat_to_tile(35.0, 139.0, 11)
        self.assertEqual(high.x // 2, low.x)
        self.assertEqual(high.y // 2, low.y)

    def test_latitude_is_clamped(self):
        coord = tiles.lonlat_to_tile(89.0, 0.0, 5)
        self.assertGreaterEqual(coord.y, 0)


class PngDecodeTest(unittest.TestCase):
    def test_rgba(self):
        data = solid_png((12, 34, 56, 200), size=4)
        width, height, pixels = tiles.decode_png(data)
        self.assertEqual((width, height), (4, 4))
        self.assertEqual(pixels[0][0], (12, 34, 56, 200))

    def test_pixel_at_reads_fixture(self):
        data = (FIXTURES / "tile_flood_0.5m.png").read_bytes()
        self.assertEqual(tiles.pixel_at(data, 5, 5), (255, 216, 192, 255))

    def test_out_of_range_pixel(self):
        with self.assertRaises(tiles.PngDecodeError):
            tiles.pixel_at(solid_png((0, 0, 0, 255), size=4), 10, 0)

    def test_rejects_non_png(self):
        with self.assertRaises(tiles.PngDecodeError):
            tiles.decode_png(b"not a png")


class GeocodingTest(unittest.TestCase):
    def setUp(self):
        self.geocoder = LocalGeocoder(FIXTURES / "geocode.json")

    def test_exact_match(self):
        point = self.geocoder.geocode("東京都世田谷区代田1-1-1")
        self.assertAlmostEqual(point.lat, 35.6595)
        self.assertAlmostEqual(point.lon, 139.6535)

    def test_prefix_match(self):
        self.assertIsNotNone(self.geocoder.geocode("東京都世田谷区代田1-1-1-101"))

    def test_unknown_address(self):
        self.assertIsNone(self.geocoder.geocode("北海道どこか"))

    def test_cache_avoids_second_call(self):
        calls = []

        class Counting:
            source = "テスト"

            def geocode(self, address):
                calls.append(address)
                return GeoPoint(35.0, 139.0, address, "テスト")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            cached = CachedGeocoder(Counting(), path)
            cached.geocode("A")
            cached.geocode("A")
            self.assertEqual(calls, ["A"])
            self.assertTrue(path.exists())
            # 別インスタンスでもキャッシュから読める
            self.assertIsNotNone(CachedGeocoder(Counting(), path).geocode("A"))
            self.assertEqual(calls, ["A"])


class LocalFrameTest(unittest.TestCase):
    def test_round_trip(self):
        frame = LocalFrame(35.6595, 139.6535)
        lon, lat = frame.to_lonlat(120.0, -80.0)
        x, y = frame.to_xy(lat, lon)
        self.assertAlmostEqual(x, 120.0, places=6)
        self.assertAlmostEqual(y, -80.0, places=6)

    def test_east_is_positive_x(self):
        frame = LocalFrame(35.0, 139.0)
        x, y = frame.to_xy(35.0, 139.001)
        self.assertGreater(x, 0)
        self.assertAlmostEqual(y, 0.0, places=6)

    def test_scale_is_realistic(self):
        frame = LocalFrame(35.0, 139.0)
        x, _ = frame.to_xy(35.0, 139.001)
        self.assertAlmostEqual(x, 91.2, delta=1.0)  # 経度0.001度 ≒ 91m


class ZoningLookupTest(unittest.TestCase):
    def setUp(self):
        self.provider = GeoJsonZoningProvider(FIXTURES / "zoning_a29.json")

    def test_lookup_inside_polygon(self):
        record = self.provider.zoning_at(35.6595, 139.6535)
        self.assertEqual(record.use_district, UseDistrict.RESIDENTIAL_1)
        self.assertAlmostEqual(record.building_coverage_ratio, 0.6)
        self.assertAlmostEqual(record.floor_area_ratio, 2.0)

    def test_lookup_second_area(self):
        record = self.provider.zoning_at(35.7400, 139.5900)
        self.assertEqual(record.use_district, UseDistrict.LOW_RISE_1)

    def test_outside_returns_none(self):
        self.assertIsNone(self.provider.zoning_at(34.0, 135.0))

    def test_property_name_variants(self):
        for properties in (
            {"youto_id": 9, "kenpei": "80", "yoseki": "500"},
            {"用途地域": "商業地域", "建蔽率": 80, "容積率": 500},
        ):
            record = parse_zoning_properties(properties, "テスト")
            self.assertEqual(record.use_district, UseDistrict.COMMERCIAL)
            self.assertAlmostEqual(record.building_coverage_ratio, 0.8)
            self.assertAlmostEqual(record.floor_area_ratio, 5.0)

    def test_ratio_accepts_both_scales(self):
        record = parse_zoning_properties({"youto_id": 1, "kenpei": 0.5, "yoseki": 1.0}, "t")
        self.assertAlmostEqual(record.building_coverage_ratio, 0.5)
        self.assertAlmostEqual(record.floor_area_ratio, 1.0)

    def test_unknown_code_returns_none(self):
        self.assertIsNone(parse_zoning_properties({"A29_004": "99"}, "テスト"))

    def test_hole_is_excluded(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
            ],
        }
        self.assertTrue(point_in_geometry(1, 1, geometry))
        self.assertFalse(point_in_geometry(5, 5, geometry))

    def test_multipolygon(self):
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]],
            ],
        }
        self.assertTrue(point_in_geometry(5.5, 5.5, geometry))
        self.assertFalse(point_in_geometry(3, 3, geometry))


class HazardLookupTest(unittest.TestCase):
    def _provider(self, flood_png=None, landslide_png=None):
        coord = tiles.lonlat_to_tile(35.6595, 139.6535, 16)
        source = {}
        if flood_png:
            source[FLOOD_L2] = {(coord.z, coord.x, coord.y): flood_png}
        if landslide_png:
            source[hazard_lookup.DEBRIS_FLOW] = {(coord.z, coord.x, coord.y): landslide_png}
        return HazardTileProvider(LocalTileSource(source))

    def test_flood_depth_from_legend(self):
        provider = self._provider(flood_png=(FIXTURES / "tile_flood_0.5m.png").read_bytes())
        hazard, results = provider.hazard_at(35.6595, 139.6535)
        self.assertAlmostEqual(hazard.flood_depth_m, 0.5)
        self.assertTrue(results[0].hit)

    def test_transparent_pixel_means_no_hazard(self):
        provider = self._provider(flood_png=(FIXTURES / "tile_transparent.png").read_bytes())
        hazard, _ = provider.hazard_at(35.6595, 139.6535)
        self.assertEqual(hazard.flood_depth_m, 0.0)

    def test_missing_tile_means_outside_area(self):
        hazard, results = self._provider().hazard_at(35.6595, 139.6535)
        self.assertEqual(hazard.flood_depth_m, 0.0)
        self.assertIn("対象区域外", results[0].note)

    def test_landslide_layer(self):
        provider = self._provider(landslide_png=solid_png((255, 237, 76, 255)))
        hazard, _ = provider.hazard_at(35.6595, 139.6535)
        self.assertTrue(hazard.landslide_risk)

    def test_unknown_colour_is_reported_not_guessed(self):
        provider = self._provider(flood_png=solid_png((3, 200, 40, 255)))
        result = provider.flood_depth(35.6595, 139.6535)
        self.assertFalse(result.hit)
        self.assertIn("凡例に一致しない", result.note)

    def test_legend_matching(self):
        self.assertEqual(match_legend((247, 245, 169, 255), hazard_lookup.FLOOD_LEGEND), 0.3)
        self.assertEqual(match_legend((255, 183, 183, 255), hazard_lookup.FLOOD_LEGEND), 3.0)
        self.assertIsNone(match_legend((255, 183, 183, 0), hazard_lookup.FLOOD_LEGEND))


class OverpassTest(unittest.TestCase):
    def setUp(self):
        data = json.loads((FIXTURES / "overpass.json").read_text(encoding="utf-8"))
        self.provider = LocalOverpassProvider(data)

    def test_buildings_and_roads_are_separated(self):
        buildings, roads = self.provider.lookup(35.6595, 139.6535)
        self.assertEqual(len(buildings), 1)
        self.assertEqual(len(roads), 2)

    def test_road_direction_uses_perpendicular_distance(self):
        _, roads = self.provider.lookup(35.6595, 139.6535)
        road = roads[0]
        self.assertEqual(road.direction, Direction.S)
        self.assertAlmostEqual(road.width_m, 6.0)
        self.assertLess(road.distance_m, 40)

    def test_roads_sorted_by_distance(self):
        _, roads = self.provider.lookup(35.6595, 139.6535)
        self.assertEqual([r.distance_m for r in roads], sorted(r.distance_m for r in roads))


class ResolverTest(unittest.TestCase):
    def setUp(self):
        coord = tiles.lonlat_to_tile(35.6595, 139.6535, 16)
        hazard = HazardTileProvider(
            LocalTileSource(
                {FLOOD_L2: {(coord.z, coord.x, coord.y): (FIXTURES / "tile_flood_0.5m.png").read_bytes()}}
            )
        )
        self.resolver = SiteResolver(
            LocalGeocoder(FIXTURES / "geocode.json"),
            GeoJsonZoningProvider(FIXTURES / "zoning_a29.json"),
            hazard,
            LocalOverpassProvider(json.loads((FIXTURES / "overpass.json").read_text(encoding="utf-8"))),
        )

    def test_resolves_all_fields(self):
        resolved = self.resolver.resolve("東京都世田谷区代田1-1-1", area_m2=180.0)
        site = resolved.site
        self.assertEqual(site.zoning.use_district, UseDistrict.RESIDENTIAL_1)
        self.assertAlmostEqual(site.zoning.building_coverage_ratio, 0.6)
        self.assertAlmostEqual(site.area_m2, 180.0, places=6)
        self.assertEqual(site.roads[0].direction, Direction.S)
        self.assertAlmostEqual(site.roads[0].width_m, 6.0)
        self.assertAlmostEqual(site.hazard.flood_depth_m, 0.5)

    def test_provenance_records_every_source(self):
        resolved = self.resolver.resolve("東京都世田谷区代田1-1-1", area_m2=180.0)
        fields = {record.field for record in resolved.provenance}
        for expected in ("所在地", "用途地域", "建蔽率", "容積率", "前面道路 幅員"):
            self.assertIn(expected, fields)
        self.assertEqual(len(resolved.site.provenance), len(resolved.provenance))

    def test_warns_about_assumptions(self):
        resolved = self.resolver.resolve("東京都世田谷区代田1-1-1", area_m2=180.0)
        joined = " ".join(resolved.warnings)
        self.assertIn("矩形近似", joined)
        self.assertIn("防火地域", joined)

    def test_unknown_address_raises(self):
        with self.assertRaises(LookupError):
            self.resolver.resolve("存在しない住所")

    def test_input_overrides_fetched_values(self):
        resolved = self.resolver.resolve(
            "東京都世田谷区代田1-1-1", area_m2=180.0, road_width_m=4.0, frontage_m=8.0
        )
        self.assertAlmostEqual(resolved.site.roads[0].width_m, 4.0)
        self.assertAlmostEqual(resolved.site.roads[0].frontage_m, 8.0)

    def test_narrow_road_is_marked_as_setback_road(self):
        resolved = self.resolver.resolve(
            "東京都世田谷区代田1-1-1", area_m2=180.0, road_width_m=3.6
        )
        self.assertTrue(resolved.site.roads[0].is_setback_road)

    def test_low_rise_district_gets_height_and_setback_defaults(self):
        resolved = self.resolver.resolve("東京都練馬区石神井町2-2-2", area_m2=140.0)
        zoning = resolved.site.zoning
        self.assertEqual(zoning.use_district, UseDistrict.LOW_RISE_1)
        self.assertEqual(zoning.height_limit_m, 10.0)
        self.assertEqual(zoning.wall_setback_m, 1.0)

    def test_missing_zoning_falls_back_with_warning(self):
        resolver = SiteResolver(LocalGeocoder(FIXTURES / "geocode.json"))
        resolved = resolver.resolve("東京都世田谷区代田1-1-1", area_m2=180.0)
        self.assertEqual(resolved.site.zoning.use_district, UseDistrict.RESIDENTIAL_1)
        self.assertTrue(any("用途地域を取得できなかった" in w for w in resolved.warnings))

    def test_rectangle_orientation_follows_the_road(self):
        """接道方位に応じて間口の向きが変わる（間口:奥行 = 0.8 の縦長）。"""
        south = rectangle_for(180.0, Direction.S)
        east = rectangle_for(180.0, Direction.E)
        south_width, south_depth = south[1][0], south[2][1]
        east_width, east_depth = east[1][0], east[2][1]
        self.assertAlmostEqual(south_width * south_depth, 180.0, places=6)
        self.assertLess(south_width, south_depth)  # 南道路 → 間口は x 方向で奥行より短い
        self.assertGreater(east_width, east_depth)  # 東道路 → 間口は y 方向
        self.assertAlmostEqual(south_width, east_depth, places=6)

    def test_resolved_site_runs_through_the_pipeline(self):
        from ai_land_design import pipeline

        resolved = self.resolver.resolve("東京都世田谷区代田1-1-1", area_m2=180.0)
        result = pipeline.run(resolved.site)
        self.assertTrue(result.envelope.buildable)
        self.assertTrue(result.code_check.ready)
        self.assertIn("データ出典", pipeline.to_markdown(result))


class BuildResolverTest(unittest.TestCase):
    def test_requires_a_geocoding_source(self):
        with self.assertRaises(ValueError):
            build_resolver(live=False)

    def test_offline_configuration(self):
        resolver, notes = build_resolver(
            live=False,
            geocode_table=str(FIXTURES / "geocode.json"),
            zoning_geojson=str(FIXTURES / "zoning_a29.json"),
        )
        self.assertIsNone(resolver.hazard)  # ネットワークを使わない
        self.assertIsNone(resolver.osm)
        self.assertTrue(any("国土数値情報" in note for note in notes))

    def test_live_configuration_registers_online_providers(self):
        resolver, notes = build_resolver(
            live=True, geocode_table=str(FIXTURES / "geocode.json")
        )
        self.assertIsNotNone(resolver.hazard)
        self.assertIsNotNone(resolver.osm)


class NetworkErrorTest(unittest.TestCase):
    def test_unreachable_host_raises_network_unavailable(self):
        with self.assertRaises(NetworkUnavailable):
            fetch("http://127.0.0.1:9/never", retries=0, timeout=1.0)


@unittest.skipUnless(LIVE, "AI_LAND_DESIGN_LIVE_TEST=1 のときだけ実行")
class LiveApiTest(unittest.TestCase):
    """実 API への疎通確認（ネットワークが使える環境でのみ実行）。"""

    def test_gsi_geocoder(self):
        from ai_land_design.sources.geocoding import GsiGeocoder

        point = GsiGeocoder().geocode("東京都世田谷区代田1-1-1")
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point.lat, 35.66, delta=0.1)
        self.assertAlmostEqual(point.lon, 139.65, delta=0.1)

    def test_hazard_tile(self):
        provider = HazardTileProvider()
        result = provider.flood_depth(35.6595, 139.6535)
        self.assertIn(result.hit, (True, False))  # 例外なく判定できること

    def test_overpass(self):
        from ai_land_design.sources.parcel import OverpassProvider

        buildings, roads = OverpassProvider().lookup(35.6595, 139.6535, radius_m=80)
        self.assertTrue(buildings or roads)


if __name__ == "__main__":
    unittest.main()
