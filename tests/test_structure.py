"""壁量計算（令46条4項）のテスト。"""

import unittest

from ai_land_design import feasibility, layout, structure
from ai_land_design.models import Building, Direction, Floor, Opening, Room, Structure
from ai_land_design.structure import (
    TABLE_LEGACY,
    SeismicTable,
    WallLine,
    confirm_table,
    elevation_area_above,
    quarter_check,
    wall_lines,
)

from .helpers import make_site


def simple_building(width=8.0, depth=10.0, storeys=2, height=2.9):
    """壁量計算の検算用に、手で面積を計算できる単純な建物をつくる。"""
    floors = []
    for storey in range(1, storeys + 1):
        footprint = [(0, 0), (width, 0), (width, depth), (0, depth)]
        rooms = [Room("LDK" if storey == 1 else "主寝室", 0, 0, width, depth, storey)]
        floors.append(Floor(storey=storey, footprint=footprint, rooms=rooms, height_m=height))
    return Building(
        structure=Structure.WOOD,
        floors=floors,
        total_floor_area_m2=width * depth * storeys,
        height_m=storeys * height + 1.6,
        ldk_type="1LDK",
        roof="陸屋根",
    )


class SeismicTableTest(unittest.TestCase):
    def test_known_coefficients(self):
        self.assertEqual(TABLE_LEGACY.coefficient(2, 1, "軽い"), 29.0)
        self.assertEqual(TABLE_LEGACY.coefficient(2, 2, "重い"), 21.0)
        self.assertEqual(TABLE_LEGACY.coefficient(1, 1, "軽い"), 11.0)

    def test_heavy_roof_needs_more_wall(self):
        for storeys, storey in ((1, 1), (2, 1), (2, 2)):
            self.assertGreater(
                TABLE_LEGACY.coefficient(storeys, storey, "重い"),
                TABLE_LEGACY.coefficient(storeys, storey, "軽い"),
            )

    def test_lower_floors_need_more_wall(self):
        self.assertGreater(
            TABLE_LEGACY.coefficient(2, 1, "軽い"), TABLE_LEGACY.coefficient(2, 2, "軽い")
        )

    def test_missing_entry_raises(self):
        with self.assertRaises(ValueError):
            TABLE_LEGACY.coefficient(5, 1, "軽い")

    def test_default_table_is_not_verified(self):
        self.assertFalse(TABLE_LEGACY.verified)
        self.assertTrue(confirm_table(TABLE_LEGACY).verified)
        self.assertFalse(TABLE_LEGACY.verified)  # 元の表は書き換わらない


class RequiredWallTest(unittest.TestCase):
    def test_seismic_requirement_is_area_times_coefficient(self):
        building = simple_building(width=8.0, depth=10.0, storeys=2)
        report = structure.evaluate(building)
        first = report.floors[0]
        self.assertAlmostEqual(first.floor_area_m2, 80.0, places=6)
        self.assertAlmostEqual(first.directions[0].required_seismic_cm, 80.0 * 29.0, places=3)

    def test_heavy_roof_increases_the_requirement(self):
        building = simple_building()
        light = structure.evaluate(building, roof_weight="軽い")
        heavy = structure.evaluate(building, roof_weight="重い")
        self.assertGreater(
            heavy.floors[0].directions[0].required_seismic_cm,
            light.floors[0].directions[0].required_seismic_cm,
        )

    def test_wind_requirement_uses_elevation_area(self):
        building = simple_building(width=8.0, depth=10.0, storeys=1, height=3.0)
        report = structure.evaluate(building)
        direction = report.floors[0].directions[0]
        self.assertAlmostEqual(
            direction.required_wind_cm, direction.elevation_area_m2 * 50.0, places=3
        )

    def test_governing_case_is_the_larger_one(self):
        report = structure.evaluate(simple_building())
        for floor in report.floors:
            for direction in floor.directions:
                self.assertEqual(
                    direction.required_cm,
                    max(direction.required_seismic_cm, direction.required_wind_cm),
                )


class ElevationAreaTest(unittest.TestCase):
    def test_excludes_the_lower_135cm(self):
        building = simple_building(width=8.0, depth=10.0, storeys=1, height=3.0)
        # 陸屋根: 壁 8.0 ×(3.0-1.35) + パラペット 9.2×0.3
        area = elevation_area_above(building, 1, "X")
        self.assertAlmostEqual(area, 8.0 * 1.65 + 9.2 * 0.3, places=3)

    def test_upper_floor_measures_from_its_own_floor_level(self):
        building = simple_building(storeys=2, height=2.9)
        first = elevation_area_above(building, 1, "X")
        second = elevation_area_above(building, 2, "X")
        self.assertGreater(first, second)

    def test_gable_roof_projects_differently_on_each_side(self):
        building = simple_building(storeys=2)
        building.roof = "切妻"
        eave_side = elevation_area_above(building, 1, "X")  # 平側（矩形）
        gable_side = elevation_area_above(building, 1, "Y")  # 妻側（三角形）
        self.assertGreater(eave_side, 0)
        self.assertGreater(gable_side, 0)

    def test_no_floors(self):
        empty = simple_building()
        empty.floors = []
        self.assertEqual(elevation_area_above(empty, 1, "X"), 0.0)


class WallLineTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)
        self.floor = self.building.floors[0]

    def test_exterior_walls_are_found_in_both_directions(self):
        for axis in ("X", "Y"):
            walls = wall_lines(self.floor, axis)
            self.assertTrue(any(w.kind == "外壁" for w in walls), axis)

    def test_openings_are_deducted(self):
        with_openings = sum(w.length_m for w in wall_lines(self.floor, "X"))
        bare = Floor(
            storey=1, footprint=self.floor.footprint, rooms=self.floor.rooms,
            height_m=self.floor.height_m, openings=[],
        )
        without = sum(w.length_m for w in wall_lines(bare, "X"))
        self.assertLess(with_openings, without)

    def test_short_segments_are_ignored(self):
        for axis in ("X", "Y"):
            for wall in wall_lines(self.floor, axis):
                self.assertGreaterEqual(wall.length_m, structure.MIN_WALL_SEGMENT_M - 1e-6)

    def test_effective_length_applies_the_magnification(self):
        wall = WallLine("X", 0.0, 0.0, 2.0, 2.5, "外壁")
        self.assertAlmostEqual(wall.effective_cm, 2.0 * 100 * 2.5)

    def test_magnifications_are_configurable(self):
        strong = sum(w.effective_cm for w in wall_lines(self.floor, "X", 5.0, 2.0))
        weak = sum(w.effective_cm for w in wall_lines(self.floor, "X", 1.0, 0.5))
        self.assertGreater(strong, weak)


class QuarterCheckTest(unittest.TestCase):
    def test_balanced_plan_passes(self):
        building = simple_building(width=8.0, depth=10.0, storeys=1)
        floor = building.floors[0]
        walls = wall_lines(floor, "X")
        ratio, detail = quarter_check(floor, "X", walls, 11.0)
        self.assertEqual(len(detail), 2)
        self.assertGreaterEqual(ratio, 0.5)

    def test_walls_on_one_side_only_fail(self):
        floor = Floor(
            storey=1,
            footprint=[(0, 0), (8, 0), (8, 10), (0, 10)],
            rooms=[Room("LDK", 0, 0, 8, 10, 1)],
            height_m=2.9,
        )
        # 南側（y=0）だけに壁があるとみなす
        walls = [WallLine("X", 0.0, 0.0, 8.0, 2.5, "外壁")]
        ratio, detail = quarter_check(floor, "X", walls, 29.0)
        self.assertEqual(detail["側端B"], 0.0)
        self.assertLess(ratio, 0.5)

    def test_both_sides_sufficient_skips_the_ratio_rule(self):
        floor = Floor(
            storey=1,
            footprint=[(0, 0), (8, 0), (8, 10), (0, 10)],
            rooms=[Room("LDK", 0, 0, 8, 10, 1)],
            height_m=2.9,
        )
        walls = [
            WallLine("X", 0.0, 0.0, 8.0, 5.0, "外壁"),
            WallLine("X", 10.0, 0.0, 8.0, 5.0, "外壁"),
        ]
        ratio, detail = quarter_check(floor, "X", walls, 11.0)
        self.assertTrue(all(v >= 1.0 for v in detail.values()))
        self.assertEqual(ratio, 1.0)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, envelope, household_size=4)
        self.report = structure.evaluate(self.building)

    def test_covers_every_floor_and_direction(self):
        self.assertEqual(len(self.report.floors), len(self.building.floors))
        for floor in self.report.floors:
            self.assertEqual([d.axis for d in floor.directions], ["X", "Y"])

    def test_quantity_and_balance_are_reported_separately(self):
        data = self.report.to_dict()
        self.assertIn("quantity_ok", data)
        self.assertIn("balance_ok", data)
        self.assertEqual(data["ok"], data["quantity_ok"] and data["balance_ok"])

    def test_generated_plan_meets_the_required_wall_quantity(self):
        self.assertTrue(self.report.quantity_ok, self.report.to_dict())
        self.assertGreaterEqual(self.report.worst_ratio, 1.0)

    def test_unverified_table_is_flagged(self):
        self.assertFalse(self.report.verified)
        text = structure.to_markdown(self.report)
        self.assertIn("要確認", text)

    def test_markdown_lists_walls_and_results(self):
        text = structure.to_markdown(self.report)
        self.assertIn("# 壁量計算書", text)
        self.assertIn("桁行方向", text)
        self.assertIn("壁率比", text)
        for floor in self.building.floors:
            self.assertIn(f"## {floor.storey}階", text)

    def test_serializable(self):
        import json

        json.dumps(self.report.to_dict(), ensure_ascii=False)

    def test_pipeline_includes_the_report(self):
        from ai_land_design import pipeline

        result = pipeline.run(self.site, pipeline.Options(roof_weight="重い"))
        self.assertIsNotNone(result.wall_quantity)
        self.assertEqual(result.wall_quantity.roof_weight, "重い")
        files = pipeline.application_package(result)
        self.assertIn("壁量計算書.md", files)
        self.assertIn("### 壁量計算", pipeline.to_markdown(result))

    def test_pipeline_skips_wall_quantity_for_non_wood(self):
        from ai_land_design import pipeline

        result = pipeline.run(self.site, pipeline.Options(structure=Structure.RC))
        self.assertIsNone(result.wall_quantity)
        self.assertNotIn("壁量計算書.md", pipeline.application_package(result))


if __name__ == "__main__":
    unittest.main()
