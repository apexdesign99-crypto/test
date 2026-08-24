import unittest

from ai_land_design import documents, feasibility, layout
from ai_land_design.models import Structure

from .helpers import make_site


class DocumentsTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)

    def test_summary_matches_building(self):
        summary = documents.application_summary(self.site, self.envelope, self.building)
        self.assertEqual(summary["階数"], self.building.storeys)
        self.assertAlmostEqual(summary["延べ面積_m2"], round(self.building.total_floor_area_m2, 2))
        self.assertEqual(summary["用途地域"], self.site.zoning.use_district.value)

    def test_compliance_ok_for_generated_plan(self):
        findings = documents.compliance_check(self.envelope, self.building)
        self.assertTrue(all(f.level != "block" for f in findings))

    def test_compliance_detects_oversized_plan(self):
        self.building.total_floor_area_m2 = self.envelope.max_floor_area_m2 * 2
        self.building.height_m = self.envelope.max_height_m + 5
        codes = {f.code for f in documents.compliance_check(self.envelope, self.building)}
        self.assertIn("OVER_FAR", codes)
        self.assertIn("OVER_HEIGHT", codes)

    def test_two_storey_wood_house_skips_structural_review(self):
        item = next(
            i for i in documents.procedures(self.site, self.building) if i.name == "構造計算適合性判定"
        )
        self.assertFalse(item.required)

    def test_rc_building_requires_structural_review(self):
        self.building.structure = Structure.RC
        item = next(
            i for i in documents.procedures(self.site, self.building) if i.name == "構造計算適合性判定"
        )
        self.assertTrue(item.required)

    def test_setback_road_triggers_negotiation(self):
        site = make_site(road_width=3.5, is_setback_road=True)
        item = next(
            i
            for i in documents.procedures(site, self.building)
            if i.name.startswith("道路後退")
        )
        self.assertTrue(item.required)

    def test_checklist_covers_required_drawings(self):
        names = [i.name for i in documents.drawing_checklist(self.building)]
        for expected in ("配置図", "各階平面図", "立面図（2面以上）", "構造図・構造計算書"):
            self.assertIn(expected, names)

    def test_markdown_contains_sections(self):
        text = documents.to_markdown(self.site, self.envelope, self.building)
        for heading in ("# 確認申請 準備資料", "## 申請概要", "## 必要な手続き", "## 適合チェック"):
            self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()
