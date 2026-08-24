import unittest

from ai_land_design.diagnosis import WEIGHTS, diagnose
from ai_land_design.models import Direction, Hazard, UseDistrict

from .helpers import make_site


class DiagnosisTest(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)

    def test_total_is_weighted_sum_of_items(self):
        result = diagnose(make_site())
        expected = sum(i.score * i.weight for i in result.items)
        self.assertAlmostEqual(result.total_score, expected)
        self.assertEqual(len(result.items), 5)

    def test_scores_stay_within_range(self):
        for site in (make_site(), make_site(road_width=2.0, frontage=2.0), make_site(far=10.0)):
            for item in diagnose(site).items:
                self.assertGreaterEqual(item.score, 0.0)
                self.assertLessEqual(item.score, 100.0)

    def test_south_road_beats_north_road(self):
        south = diagnose(make_site(road_direction=Direction.S))
        north = diagnose(make_site(road_direction=Direction.N))
        self.assertGreater(south.total_score, north.total_score)

    def test_hazard_lowers_location_score(self):
        clean = diagnose(make_site())
        risky = diagnose(make_site(hazard=Hazard(flood_depth_m=2.0, landslide_risk=True)))
        self.assertLess(risky.total_score, clean.total_score)
        self.assertTrue(any(f.code == "DIAG_FLOOD" for f in risky.findings))
        self.assertTrue(any(f.code == "DIAG_LANDSLIDE" for f in risky.findings))

    def test_cheap_land_scores_better_than_expensive(self):
        cheap = diagnose(make_site(land_price_jpy=60_000_000), market_unit_price_per_tsubo=1_500_000)
        pricey = diagnose(
            make_site(land_price_jpy=180_000_000), market_unit_price_per_tsubo=1_500_000
        )
        self.assertGreater(cheap.total_score, pricey.total_score)

    def test_rank_is_ordered(self):
        good = diagnose(make_site(bcr=0.8, far=4.0, road_width=8.0, station_distance_m=300))
        poor = diagnose(
            make_site(
                bcr=0.4,
                far=0.8,
                road_width=2.5,
                frontage=2.5,
                station_distance_m=2200,
                hazard=Hazard(flood_depth_m=1.5, landslide_risk=True, liquefaction_risk=True),
            )
        )
        self.assertGreater(good.total_score, poor.total_score)
        self.assertIn(good.rank, ("S", "A", "B"))
        self.assertIn(poor.rank, ("C", "D"))

    def test_industrial_zone_is_flagged(self):
        result = diagnose(make_site(use_district=UseDistrict.EXCLUSIVE_INDUSTRIAL))
        self.assertTrue(any(f.code == "DIAG_USE" and f.level == "block" for f in result.findings))

    def test_serializable(self):
        data = diagnose(make_site()).to_dict()
        self.assertIn("total_score", data)
        self.assertEqual(len(data["items"]), 5)


if __name__ == "__main__":
    unittest.main()
