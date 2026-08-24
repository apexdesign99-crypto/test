import unittest
from pathlib import Path

from ai_land_design.sources.gis import LocalGisProvider, site_from_dict
from ai_land_design.sources.realestate import LocalRealEstateProvider

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


class RealEstateProviderTest(unittest.TestCase):
    def setUp(self):
        self.provider = LocalRealEstateProvider(SAMPLES / "listings.json")

    def test_search_filters_by_address(self):
        hits = self.provider.search("世田谷")
        self.assertTrue(hits)
        self.assertTrue(all("世田谷" in l.address for l in hits))

    def test_search_respects_limit(self):
        self.assertEqual(len(self.provider.search("", limit=2)), 2)

    def test_get_by_id(self):
        listing = self.provider.get("L001")
        self.assertIsNotNone(listing)
        self.assertEqual(listing.address, "東京都世田谷区代田1-1-1")
        self.assertIsNone(self.provider.get("missing"))

    def test_unit_price_conversion(self):
        listing = self.provider.get("L001")
        self.assertAlmostEqual(
            listing.unit_price_per_tsubo,
            round(listing.price_jpy / (listing.area_m2 / 3.305785)),
            delta=1,
        )

    def test_median_unit_price(self):
        median = self.provider.median_unit_price("世田谷")
        prices = sorted(l.unit_price_per_tsubo for l in self.provider.search("世田谷"))
        self.assertEqual(median, (prices[1] + prices[2]) // 2)

    def test_median_returns_none_for_unknown_area(self):
        self.assertIsNone(self.provider.median_unit_price("北海道"))


class GisProviderTest(unittest.TestCase):
    def setUp(self):
        self.provider = LocalGisProvider(SAMPLES / "sites.json")

    def test_all_sites_parse(self):
        sites = self.provider.all_sites()
        self.assertEqual(len(sites), 4)
        for site in sites:
            self.assertGreater(site.area_m2, 0)

    def test_lookup_by_id_and_address(self):
        by_id = self.provider.site_for("setagaya")
        by_address = self.provider.site_for("世田谷区代田")
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id.site_id, by_address.site_id)
        self.assertIsNone(self.provider.site_for("存在しない"))

    def test_zoning_and_roads_are_mapped(self):
        site = self.provider.site_for("nerima_lowrise")
        self.assertEqual(site.zoning.use_district.value, "第一種低層住居専用地域")
        self.assertEqual(site.zoning.height_limit_m, 10.0)
        self.assertTrue(site.roads[0].is_setback_road)
        self.assertEqual(site.roads[0].direction.value, "北")

    def test_site_from_dict_defaults(self):
        site = site_from_dict(
            {
                "site_id": "minimal",
                "address": "テスト",
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "zoning": {
                    "use_district": "商業地域",
                    "building_coverage_ratio": 0.8,
                    "floor_area_ratio": 4.0,
                },
            }
        )
        self.assertEqual(site.area_m2, 100.0)
        self.assertEqual(site.roads, [])
        self.assertEqual(site.zoning.fire_zone.value, "指定なし")

    def test_unknown_enum_raises(self):
        with self.assertRaises(ValueError):
            site_from_dict(
                {
                    "polygon": [[0, 0], [1, 0], [1, 1]],
                    "zoning": {
                        "use_district": "架空地域",
                        "building_coverage_ratio": 0.5,
                        "floor_area_ratio": 1.0,
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
