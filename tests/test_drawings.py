import unittest
import xml.etree.ElementTree as ET

from ai_land_design import drawings, feasibility, layout
from ai_land_design.models import Direction

from .helpers import make_site


class DrawingsTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)

    def _parse(self, svg):
        """SVG として妥当か（XML パースできるか）を確認して要素を返す。"""
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        return root

    def test_site_plan_contains_site_and_building(self):
        svg = drawings.site_plan_svg(self.site, self.building, self.envelope)
        root = self._parse(svg)
        self.assertIn("配置図", svg)
        self.assertIn("道路 幅員", svg)
        self.assertIn("敷地面積", svg)
        self.assertGreaterEqual(len(root.findall(".//{http://www.w3.org/2000/svg}polygon")), 2)

    def test_site_plan_shows_setback_line_for_narrow_road(self):
        site = make_site(road_width=3.0, is_setback_road=True)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        svg = drawings.site_plan_svg(site, building, envelope)
        self.assertIn("セットバック", svg)

    def test_all_four_elevations(self):
        elevations = drawings.all_elevations_svg(self.site, self.building)
        self.assertEqual(set(elevations), {"南", "東", "北", "西"})
        for name, svg in elevations.items():
            self._parse(svg)
            self.assertIn(f"{name}立面図", svg)
            self.assertIn("GL", svg)
            self.assertIn("軒高", svg)

    def test_road_facing_elevation_shows_road_slant(self):
        svg = drawings.elevation_svg(self.site, self.building, Direction.S)
        self.assertIn("道路斜線", svg)

    def test_elevation_draws_openings_of_that_facade(self):
        south = drawings.elevation_svg(self.site, self.building, Direction.S)
        south_openings = [
            o for f in self.building.floors for o in f.openings if o.facade is Direction.S
        ]
        self.assertTrue(south_openings)
        root = self._parse(south)
        # 外壁・基礎・屋根・開口部が矩形として描かれている
        polygons = root.findall(".//{http://www.w3.org/2000/svg}polygon")
        self.assertGreaterEqual(len(polygons), 3 + len(south_openings))

    def test_section_shows_storey_heights(self):
        svg = drawings.section_svg(self.site, self.building)
        self._parse(svg)
        self.assertIn("断面図", svg)
        self.assertIn("天井高", svg)
        self.assertIn("最高の高さ", svg)
        for floor in self.building.floors:
            self.assertIn(f"{floor.storey}階", svg)

    def test_area_calculation_lists_triangles_and_totals(self):
        svg = drawings.area_calculation_svg(self.site, self.building)
        self._parse(svg)
        self.assertIn("求積図", svg)
        self.assertIn("敷地面積 合計", svg)
        self.assertIn("延べ面積 合計", svg)
        self.assertIn(f"{self.site.area_m2:.2f} m²", svg)

    def test_area_calculation_triangles_sum_to_site_area(self):
        triangles = drawings._triangulate(self.site.polygon)
        total = sum(drawings._triangle_area(t) for t in triangles)
        self.assertAlmostEqual(total, self.site.area_m2, places=6)

    def test_irregular_site_is_triangulated(self):
        site = make_site()
        site.polygon = [(0, 0), (16, 0), (16, 9), (4, 9), (4, 14), (0, 14)]
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        triangles = drawings._triangulate(site.polygon)
        self.assertEqual(len(triangles), 4)
        svg = drawings.area_calculation_svg(site, building)
        self.assertIn("△4", svg)

    def test_all_drawings_set(self):
        files = drawings.all_drawings(self.site, self.building, self.envelope)
        expected = {
            "site_plan.svg",
            "plan_1f.svg",
            "plan_2f.svg",
            "elevation_南.svg",
            "elevation_東.svg",
            "elevation_北.svg",
            "elevation_西.svg",
            "section.svg",
            "area_calculation.svg",
        }
        self.assertEqual(set(files), expected)
        for svg in files.values():
            self._parse(svg)

    def test_drawings_carry_disclaimer(self):
        svg = drawings.site_plan_svg(self.site, self.building, self.envelope)
        self.assertIn("建築士による確認が必要", svg)


if __name__ == "__main__":
    unittest.main()
