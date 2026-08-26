import unittest

from ai_land_design import compliance, feasibility, layout
from ai_land_design.compliance import CHECK, FAIL, PASS
from ai_land_design.models import Direction, FireZone, Structure, UseDistrict

from .helpers import make_site


def build(site, **kwargs):
    envelope = feasibility.evaluate(site)
    building = layout.generate(site, envelope, **kwargs)
    return envelope, building


class ComplianceTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope, self.building = build(self.site, household_size=4)
        self.report = compliance.evaluate(self.site, self.envelope, self.building)

    def _item(self, name):
        return next(i for i in self.report.items if i.name == name)

    def test_generated_plan_has_no_violations(self):
        self.assertTrue(self.report.ready, [i.name for i in self.report.failed])
        self.assertEqual(self.report.failed, [])

    def test_every_item_has_law_reference(self):
        for item in self.report.items:
            self.assertTrue(item.law)
            self.assertIn(item.result, (PASS, FAIL, CHECK))

    def test_summary_counts_match_items(self):
        data = self.report.to_dict()
        self.assertEqual(
            data["summary"]["適合"] + data["summary"]["不適合"] + data["summary"]["要確認"],
            len(self.report.items),
        )

    def test_coverage_and_far_are_checked_against_limits(self):
        coverage = self._item("建蔽率")
        far = self._item("容積率")
        self.assertEqual(coverage.result, PASS)
        self.assertEqual(far.result, PASS)
        self.assertIn("%", coverage.actual)

    def test_road_access_failure_is_detected(self):
        site = make_site(frontage=1.5)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        report = compliance.evaluate(site, envelope, building)
        access = next(i for i in report.items if i.name == "接道義務")
        self.assertEqual(access.result, FAIL)
        self.assertFalse(report.ready)

    def test_industrial_zone_blocks_dwelling(self):
        site = make_site(use_district=UseDistrict.EXCLUSIVE_INDUSTRIAL)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        report = compliance.evaluate(site, envelope, building)
        self.assertEqual(next(i for i in report.items if i.name == "用途制限").result, FAIL)

    def test_daylight_is_checked_per_habitable_room(self):
        names = [i.name for i in self.report.items if "の採光" in i.name]
        habitable = [
            f"{f.storey}階 {r.name} の採光"
            for f in self.building.floors
            for r in f.rooms
            if r.is_habitable
        ]
        self.assertEqual(sorted(names), sorted(habitable))

    def test_daylight_uses_one_seventh_rule(self):
        for floor in self.building.floors:
            for name, area_m2, window_area, ok in compliance.daylight_check(floor):
                self.assertEqual(ok, window_area >= area_m2 / 7)

    def test_stair_dimensions_meet_code(self):
        width, riser, tread, steps = compliance.stair_dimensions(self.building)
        self.assertGreaterEqual(width, compliance.MIN_STAIR_WIDTH_M)
        self.assertLessEqual(riser, compliance.MAX_RISER_M + 1e-6)
        self.assertGreaterEqual(tread, compliance.MIN_TREAD_M)
        self.assertGreater(steps, 10)
        self.assertEqual(self._item("階段の寸法").result, PASS)

    def test_low_ceiling_is_rejected(self):
        site = make_site()
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope, ceiling_height_m=2.0)
        report = compliance.evaluate(site, envelope, building)
        self.assertEqual(next(i for i in report.items if i.name == "居室の天井高さ").result, FAIL)

    def test_wall_setback_is_measured_from_geometry(self):
        site = make_site(use_district=UseDistrict.LOW_RISE_1, far=1.0, wall_setback_m=1.0,
                         height_limit_m=10.0)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        report = compliance.evaluate(site, envelope, building)
        item = next(i for i in report.items if i.name == "外壁の後退距離")
        self.assertEqual(item.result, PASS)
        setbacks = compliance.actual_setbacks(site, building)
        self.assertGreaterEqual(min(setbacks.values()), 1.0)

    def test_fire_zone_requires_confirmation(self):
        site = make_site(fire_zone=FireZone.FIRE)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        report = compliance.evaluate(site, envelope, building)
        item = next(i for i in report.items if i.name == "防火地域内の構造制限")
        self.assertEqual(item.result, CHECK)

    def test_shadow_regulation_is_flagged(self):
        site = make_site(shadow_regulation=True)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        report = compliance.evaluate(site, envelope, building)
        self.assertTrue(any(i.name == "日影規制" and i.result == CHECK for i in report.items))

    def test_structural_and_energy_are_out_of_scope(self):
        for name in ("構造安全性（壁量計算以外）", "省エネ基準適合"):
            self.assertEqual(self._item(name).result, CHECK)

    def test_wall_quantity_items_appear_when_a_report_is_given(self):
        from ai_land_design import structure

        report = structure.evaluate(self.building)
        checked = compliance.evaluate(self.site, self.envelope, self.building, report)
        names = [i.name for i in checked.items]
        self.assertTrue(any("壁量" in name for name in names))
        self.assertTrue(any("壁の配置" in name for name in names))

    def test_wall_quantity_is_unverified_until_the_table_is_confirmed(self):
        from ai_land_design import structure

        report = structure.evaluate(self.building)
        checked = compliance.evaluate(self.site, self.envelope, self.building, report)
        item = next(i for i in checked.items if "壁量" in i.name)
        self.assertEqual(item.result, CHECK)

        confirmed = structure.evaluate(self.building, table=structure.confirm_table(structure.TABLE_LEGACY))
        checked = compliance.evaluate(self.site, self.envelope, self.building, confirmed)
        item = next(i for i in checked.items if "壁量" in i.name)
        self.assertEqual(item.result, PASS)

    def test_non_wood_structure_is_out_of_scope_for_wall_quantity(self):
        self.building.structure = Structure.RC
        items = compliance.wall_quantity_items(self.building, None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].result, CHECK)
        self.assertIn("木造軸組構法のみ", items[0].actual)

    def test_markdown_lists_failures_first(self):
        site = make_site(frontage=1.5)
        envelope = feasibility.evaluate(site)
        report = compliance.evaluate(site, envelope, layout.generate(site, envelope))
        text = compliance.to_markdown(report)
        self.assertIn("## 不適合", text)
        self.assertIn("接道義務", text)


if __name__ == "__main__":
    unittest.main()
