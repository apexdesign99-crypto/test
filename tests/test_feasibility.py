import unittest

from ai_land_design import feasibility
from ai_land_design.models import Direction, FireZone, UseDistrict

from .helpers import make_site


class RoadAccessTest(unittest.TestCase):
    def test_sufficient_frontage_passes(self):
        ok, findings = feasibility.check_road_access(make_site())
        self.assertTrue(ok)
        self.assertEqual(findings, [])

    def test_short_frontage_blocks(self):
        ok, findings = feasibility.check_road_access(make_site(frontage=1.8))
        self.assertFalse(ok)
        self.assertEqual(findings[0].code, "ROAD_ACCESS")
        self.assertEqual(findings[0].level, "block")

    def test_no_legal_road_blocks(self):
        ok, findings = feasibility.check_road_access(make_site(is_legal_road=False))
        self.assertFalse(ok)
        self.assertEqual(findings[0].code, "NO_ROAD")

    def test_narrow_road_with_setback_still_counts(self):
        # 42条2項道路はセットバック後に幅員4mとみなされる
        ok, _ = feasibility.check_road_access(make_site(road_width=3.6, is_setback_road=True))
        self.assertTrue(ok)


class SetbackTest(unittest.TestCase):
    def test_setback_area_is_center_line_offset(self):
        site = make_site(road_width=3.0, frontage=10.0, is_setback_road=True)
        loss, findings = feasibility.setback_loss_m2(site)
        self.assertAlmostEqual(loss, 0.5 * 10.0)  # (4.0-3.0)/2 × 10m
        self.assertEqual(findings[0].code, "SETBACK_42_2")

    def test_wide_road_has_no_setback(self):
        loss, findings = feasibility.setback_loss_m2(make_site(road_width=6.0))
        self.assertEqual(loss, 0.0)
        self.assertEqual(findings, [])


class CoverageTest(unittest.TestCase):
    def test_corner_lot_relaxation(self):
        bcr, _ = feasibility.applied_coverage_ratio(make_site(bcr=0.6, is_corner_lot=True))
        self.assertAlmostEqual(bcr, 0.7)

    def test_fire_zone_relaxation(self):
        bcr, _ = feasibility.applied_coverage_ratio(make_site(bcr=0.6, fire_zone=FireZone.FIRE))
        self.assertAlmostEqual(bcr, 0.7)

    def test_fire_zone_with_80_percent_becomes_unlimited(self):
        bcr, findings = feasibility.applied_coverage_ratio(
            make_site(bcr=0.8, fire_zone=FireZone.FIRE)
        )
        self.assertAlmostEqual(bcr, 1.0)
        self.assertEqual(findings[0].code, "BCR_100")

    def test_relaxation_never_exceeds_100_percent(self):
        bcr, _ = feasibility.applied_coverage_ratio(
            make_site(bcr=0.9, is_corner_lot=True, fire_zone=FireZone.QUASI)
        )
        self.assertLessEqual(bcr, 1.0)


class FloorAreaRatioTest(unittest.TestCase):
    def test_narrow_road_limits_far_in_residential(self):
        far, findings = feasibility.applied_far(make_site(road_width=4.0, far=2.0))
        self.assertAlmostEqual(far, 1.6)  # 4.0 × 0.4
        self.assertEqual(findings[0].code, "FAR_ROAD_LIMIT")

    def test_commercial_uses_06_coefficient(self):
        far, _ = feasibility.applied_far(
            make_site(use_district=UseDistrict.COMMERCIAL, road_width=6.0, far=5.0)
        )
        self.assertAlmostEqual(far, 3.6)  # 6.0 × 0.6

    def test_wide_road_keeps_designated_far(self):
        far, findings = feasibility.applied_far(make_site(road_width=12.0, far=2.0))
        self.assertAlmostEqual(far, 2.0)
        self.assertEqual(findings, [])

    def test_designated_far_wins_when_smaller(self):
        far, findings = feasibility.applied_far(make_site(road_width=8.0, far=1.0))
        self.assertAlmostEqual(far, 1.0)
        self.assertEqual(findings, [])


class HeightLimitTest(unittest.TestCase):
    def _limit(self, limits, name):
        return next(l for l in limits if l.name == name)

    def test_low_rise_gets_absolute_height(self):
        site = make_site(use_district=UseDistrict.LOW_RISE_1, far=1.0, height_limit_m=10.0)
        limits = feasibility.height_limits(site)
        self.assertAlmostEqual(self._limit(limits, "絶対高さ制限").limit_m, 10.0)

    def test_road_slant_uses_setback_relaxation(self):
        site = make_site(road_width=4.0)
        limits = feasibility.height_limits(site, wall_setback_m=1.0)
        # 1.25 × (4.0 + 1.0×2)
        self.assertAlmostEqual(self._limit(limits, "道路斜線制限").limit_m, 7.5)

    def test_commercial_road_slant_is_steeper(self):
        site = make_site(use_district=UseDistrict.COMMERCIAL, road_width=10.0, far=5.0)
        limits = feasibility.height_limits(site, wall_setback_m=0.5)
        self.assertAlmostEqual(self._limit(limits, "道路斜線制限").limit_m, 1.5 * 11.0)

    def test_road_slant_not_applied_beyond_applicable_distance(self):
        site = make_site(road_width=25.0, far=2.0)
        limits = feasibility.height_limits(site, wall_setback_m=0.5)
        self.assertGreaterEqual(self._limit(limits, "道路斜線制限").limit_m, 999.0)

    def test_north_slant_only_in_residential_exclusive_districts(self):
        low = feasibility.height_limits(
            make_site(use_district=UseDistrict.LOW_RISE_1, far=1.0), wall_setback_m=1.0
        )
        commercial = feasibility.height_limits(
            make_site(use_district=UseDistrict.COMMERCIAL, far=5.0)
        )
        self.assertTrue(any(l.name == "北側斜線制限" for l in low))
        self.assertFalse(any(l.name == "北側斜線制限" for l in commercial))

    def test_applicable_distance_table(self):
        self.assertEqual(feasibility.applicable_distance_m(UseDistrict.RESIDENTIAL_1, 2.0), 20.0)
        self.assertEqual(feasibility.applicable_distance_m(UseDistrict.RESIDENTIAL_1, 4.0), 30.0)
        self.assertEqual(feasibility.applicable_distance_m(UseDistrict.COMMERCIAL, 5.0), 25.0)


class EnvelopeTest(unittest.TestCase):
    def test_standard_site_is_buildable(self):
        env = feasibility.evaluate(make_site())
        self.assertTrue(env.buildable)
        self.assertAlmostEqual(env.max_building_area_m2, 224.0 * 0.6)
        self.assertGreaterEqual(env.max_storeys, 2)

    def test_floor_area_capped_by_building_area_times_storeys(self):
        env = feasibility.evaluate(make_site(far=5.0, road_width=12.0))
        self.assertLessEqual(
            env.max_floor_area_m2, env.max_building_area_m2 * env.max_storeys + 1e-6
        )

    def test_flag_lot_without_access_is_not_buildable(self):
        env = feasibility.evaluate(make_site(frontage=1.8))
        self.assertFalse(env.buildable)
        self.assertTrue(any(f.code == "ROAD_ACCESS" for f in env.findings))

    def test_exclusive_industrial_blocks_dwelling(self):
        env = feasibility.evaluate(make_site(use_district=UseDistrict.EXCLUSIVE_INDUSTRIAL))
        self.assertFalse(env.buildable)
        self.assertTrue(any(f.code == "USE_NOT_ALLOWED" for f in env.findings))

    def test_setback_reduces_effective_area(self):
        env = feasibility.evaluate(make_site(road_width=3.0, is_setback_road=True))
        self.assertLess(env.effective_site_area_m2, 224.0)
        self.assertGreater(env.setback_loss_m2, 0.0)

    def test_shadow_regulation_is_reported(self):
        env = feasibility.evaluate(make_site(shadow_regulation=True))
        self.assertTrue(any(f.code == "SHADOW" for f in env.findings))


if __name__ == "__main__":
    unittest.main()
