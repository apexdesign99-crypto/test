import unittest

from ai_land_design import application, compliance, drawings, feasibility, layout
from ai_land_design.application import ApplicationInfo, Party

from .helpers import make_site


class ApplicationTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)
        self.info = ApplicationInfo(
            owner=Party(name="山田 太郎", address="東京都テスト区1-1-1", phone="03-0000-0000"),
            designer=Party(
                name="設計 花子", qualification="一級建築士", registration="第00000号",
                office="テスト設計事務所",
            ),
            start_date="2026-10-01",
            completion_date="2027-03-31",
        )

    def test_five_sheets(self):
        sheets = application.sheets(self.site, self.envelope, self.building, self.info)
        self.assertEqual(len(sheets), 5)
        for title in ("第一面", "第二面", "第三面", "第四面", "第五面"):
            self.assertTrue(any(title in key for key in sheets))

    def test_sheet_3_matches_the_plan(self):
        rows = dict(application.sheet_3(self.site, self.envelope, self.building, self.info))
        self.assertEqual(rows["用途地域"], self.site.zoning.use_district.value)
        self.assertIn(f"{self.building.footprint_area_m2:.2f}", rows["建築面積"])
        self.assertIn(f"{self.building.total_floor_area_m2:.2f}", rows["延べ面積"])
        self.assertIn(f"地上 {self.building.storeys} 階", rows["建築物の階数"])
        self.assertIn(f"{self.building.height_m:.2f}", rows["最高の高さ"])

    def test_missing_parties_are_marked_blank(self):
        rows = dict(application.sheet_1(ApplicationInfo()))
        self.assertEqual(rows["建築主 氏名"], application.BLANK)

    def test_supplied_parties_are_used(self):
        rows = dict(application.sheet_1(self.info))
        self.assertEqual(rows["建築主 氏名"], "山田 太郎")
        self.assertEqual(rows["設計者 資格"], "一級建築士")

    def test_sheet_4_lists_daylight_per_room(self):
        rows = dict(application.sheet_4(self.site, self.building, self.info))
        habitable = [r.name for f in self.building.floors for r in f.rooms if r.is_habitable]
        for name in habitable:
            self.assertTrue(any(name in key and "採光" in key for key in rows))

    def test_sheet_5_totals_match(self):
        rows = dict(application.sheet_5(self.building, self.info))
        self.assertIn(f"{self.building.total_floor_area_m2:.2f}", rows["合計 床面積"])

    def test_setback_is_disclosed_in_site_area(self):
        site = make_site(road_width=3.0, is_setback_road=True)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        rows = dict(application.sheet_3(site, envelope, building, self.info))
        self.assertIn("道路後退", rows["敷地面積"])

    def test_html_is_printable_and_complete(self):
        report = compliance.evaluate(self.site, self.envelope, self.building)
        files = drawings.all_drawings(self.site, self.building, self.envelope)
        html = application.to_html(self.site, self.envelope, self.building, self.info, report, files)
        self.assertIn("<!doctype html>", html)
        self.assertIn("@page", html)  # 印刷用のページ設定
        self.assertIn("法適合チェック", html)
        self.assertIn("配置図", html)
        self.assertIn("様式そのものではありません", html)
        self.assertEqual(html.count("<svg"), len(files))

    def test_markdown_sheets(self):
        text = application.to_markdown(self.site, self.envelope, self.building, self.info)
        self.assertIn("# 確認申請 記載事項シート", text)
        self.assertIn("第三面", text)

    def test_requires_a_buildable_plan(self):
        site = make_site(frontage=1.5)
        envelope = feasibility.evaluate(site)
        empty = layout.generate(site, envelope)
        empty.floors = []
        with self.assertRaises(ValueError):
            application.sheets(site, envelope, empty)


if __name__ == "__main__":
    unittest.main()
