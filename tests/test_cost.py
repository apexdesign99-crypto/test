import unittest

from ai_land_design import cost, feasibility, layout
from ai_land_design.cost import Rates
from ai_land_design.models import Structure

from .helpers import make_site


class CostTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site(land_price_jpy=90_000_000)
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)

    def test_main_cost_follows_unit_cost_and_area(self):
        items = cost.cost_items(100.0, Structure.WOOD, "標準")
        tsubo = 100.0 / cost.TSUBO_M2
        expected = round(tsubo * cost.UNIT_COST_PER_TSUBO[Structure.WOOD])
        self.assertEqual(items[0].amount_jpy, expected)

    def test_actual_cost_matches_the_reference_figure(self):
        """実績（延床35坪・本体工事原価1,600万円）と一致すること。"""
        items = cost.cost_items(35 * cost.TSUBO_M2, Structure.WOOD, "標準")
        self.assertAlmostEqual(items[0].amount_jpy, 16_000_000, delta=50_000)

    def test_custom_unit_cost_overrides_the_default(self):
        items = cost.cost_items(
            35 * cost.TSUBO_M2, Structure.WOOD, "標準", unit_cost_per_tsubo=600_000
        )
        self.assertAlmostEqual(items[0].amount_jpy, 21_000_000, delta=50_000)

    def test_margin_is_a_share_of_the_contract_price(self):
        margin = cost.margin_for(15_000_000, Rates(gross_margin=0.25))
        contract = 15_000_000 + margin
        self.assertAlmostEqual(margin / contract, 0.25, places=4)

    def test_zero_margin(self):
        self.assertEqual(cost.margin_for(15_000_000, Rates(gross_margin=0.0)), 0)

    def test_grade_scales_main_cost(self):
        low = cost.cost_items(100.0, Structure.WOOD, "ローコスト")[0].amount_jpy
        standard = cost.cost_items(100.0, Structure.WOOD, "標準")[0].amount_jpy
        high = cost.cost_items(100.0, Structure.WOOD, "ハイグレード")[0].amount_jpy
        self.assertLess(low, standard)
        self.assertLess(standard, high)

    def test_rc_is_more_expensive_than_wood(self):
        wood = cost.cost_items(100.0, Structure.WOOD)[0].amount_jpy
        rc = cost.cost_items(100.0, Structure.RC)[0].amount_jpy
        self.assertGreater(rc, wood)

    def test_tax_and_totals_are_consistent(self):
        breakdown = cost.estimate(self.site, self.building)
        self.assertEqual(
            breakdown.cost_subtotal_jpy,
            sum(i.amount_jpy for i in breakdown.construction_items),
        )
        self.assertEqual(
            breakdown.contract_jpy, breakdown.cost_subtotal_jpy + breakdown.margin_jpy
        )
        self.assertEqual(
            breakdown.construction_subtotal_jpy,
            breakdown.contract_jpy + breakdown.soft_subtotal_jpy,
        )
        self.assertEqual(
            breakdown.construction_tax_jpy,
            round(breakdown.construction_subtotal_jpy * 0.10),
        )
        self.assertEqual(
            breakdown.construction_total_jpy,
            breakdown.construction_subtotal_jpy + breakdown.construction_tax_jpy,
        )
        self.assertEqual(
            breakdown.project_total_jpy,
            breakdown.land_price_jpy
            + breakdown.construction_total_jpy
            + breakdown.other_total_jpy,
        )

    def test_brokerage_uses_speed_formula(self):
        items = cost.other_items(100_000_000, 40_000_000)
        brokerage = next(i for i in items if i.name == "仲介手数料")
        self.assertEqual(brokerage.amount_jpy, round((100_000_000 * 0.03 + 60_000) * 1.1))

    def test_no_land_price_skips_brokerage(self):
        names = [i.name for i in cost.other_items(0, 40_000_000)]
        self.assertNotIn("仲介手数料", names)
        self.assertIn("登記費用", names)

    def test_land_price_override(self):
        breakdown = cost.estimate(self.site, self.building, land_price_jpy=120_000_000)
        self.assertEqual(breakdown.land_price_jpy, 120_000_000)

    def test_custom_rates_change_result(self):
        base = cost.estimate(self.site, self.building)
        lean = cost.estimate(
            self.site, self.building, rates=Rates(incidental=0.05, design=0.05, contingency=0.0)
        )
        self.assertLess(lean.project_total_jpy, base.project_total_jpy)

    def test_unit_cost_per_tsubo_is_positive(self):
        breakdown = cost.estimate(self.site, self.building)
        unit = cost.unit_cost_per_tsubo(breakdown, self.building.total_floor_area_m2)
        self.assertGreater(unit, 0)
        self.assertLess(unit, 5_000_000)

    def test_cost_unit_price_is_lower_than_the_contract_unit_price(self):
        breakdown = cost.estimate(self.site, self.building)
        area = self.building.total_floor_area_m2
        self.assertLess(
            cost.unit_cost_price_per_tsubo(breakdown, area),
            cost.unit_cost_per_tsubo(breakdown, area),
        )

    def test_margin_rate_flows_through_the_pipeline(self):
        from ai_land_design import pipeline

        lean = pipeline.run(self.site, pipeline.Options(gross_margin=0.10))
        rich = pipeline.run(self.site, pipeline.Options(gross_margin=0.35))
        self.assertLess(lean.cost.margin_jpy, rich.cost.margin_jpy)
        self.assertAlmostEqual(lean.cost.margin_rate, 0.10, places=3)
        self.assertAlmostEqual(rich.cost.margin_rate, 0.35, places=3)

    def test_unit_cost_option_flows_through_the_pipeline(self):
        from ai_land_design import pipeline

        cheap = pipeline.run(self.site, pipeline.Options(unit_cost_per_tsubo=400_000))
        expensive = pipeline.run(self.site, pipeline.Options(unit_cost_per_tsubo=700_000))
        self.assertLess(cheap.cost.cost_subtotal_jpy, expensive.cost.cost_subtotal_jpy)


if __name__ == "__main__":
    unittest.main()
