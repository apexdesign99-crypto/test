import unittest

from ai_land_design import feasibility, layout
from ai_land_design.models import Structure, UseDistrict

from .helpers import make_site


def overlaps(a, b) -> bool:
    return (
        a.x < b.x + b.w - 1e-6
        and b.x < a.x + a.w - 1e-6
        and a.y < b.y + b.h - 1e-6
        and b.y < a.y + a.h - 1e-6
    )


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)

    def test_rooms_tile_the_floor_without_overlap(self):
        for floor in self.building.floors:
            total = sum(r.area_m2 for r in floor.rooms)
            self.assertAlmostEqual(total, floor.area_m2, places=6)
            for i, a in enumerate(floor.rooms):
                for b in floor.rooms[i + 1 :]:
                    self.assertFalse(overlaps(a, b), f"{a.name} と {b.name} が重なっている")

    def test_rooms_stay_inside_footprint(self):
        for floor in self.building.floors:
            xs = [p[0] for p in floor.footprint]
            ys = [p[1] for p in floor.footprint]
            for room in floor.rooms:
                self.assertGreaterEqual(room.x, min(xs) - 1e-6)
                self.assertGreaterEqual(room.y, min(ys) - 1e-6)
                self.assertLessEqual(room.x + room.w, max(xs) + 1e-6)
                self.assertLessEqual(room.y + room.h, max(ys) + 1e-6)

    def test_respects_envelope_limits(self):
        self.assertLessEqual(
            self.building.footprint_area_m2, self.envelope.max_building_area_m2 + 1e-6
        )
        self.assertLessEqual(
            self.building.total_floor_area_m2, self.envelope.max_floor_area_m2 + 1e-6
        )
        self.assertLessEqual(self.building.height_m, self.envelope.max_height_m + 1e-6)

    def test_household_size_drives_room_count(self):
        small = layout.generate(self.site, self.envelope, household_size=2)
        large = layout.generate(self.site, self.envelope, household_size=5)
        small_rooms = sum(len(f.rooms) for f in small.floors)
        large_rooms = sum(len(f.rooms) for f in large.floors)
        self.assertGreater(large_rooms, small_rooms)
        self.assertGreater(large.total_floor_area_m2, small.total_floor_area_m2)

    def test_ldk_type_reported(self):
        self.assertTrue(self.building.ldk_type.endswith("LDK"))

    def test_wet_areas_are_capped(self):
        rooms = {r.name: r for f in self.building.floors for r in f.rooms}
        self.assertLessEqual(rooms["浴室"].area_m2, 5.0 + 1e-6)
        self.assertLessEqual(rooms["トイレ"].area_m2, 2.5 + 1e-6)

    def test_all_dimensions_are_on_the_910_grid(self):
        """室の寸法と、建物原点からの位置がすべて 910mm の倍数であること。"""
        from ai_land_design.geometry import bbox

        for floor in self.building.floors:
            ox, oy, _, _ = bbox(floor.footprint)
            for room in floor.rooms:
                for value in (room.x - ox, room.y - oy, room.w, room.h):
                    steps = value / layout.GRID_M
                    self.assertAlmostEqual(
                        steps, round(steps), places=6, msg=f"{room.name}: {value}"
                    )

    def test_stairs_align_across_floors(self):
        stairs = [f.room("階段") for f in self.building.floors]
        self.assertTrue(all(s is not None for s in stairs))
        first = stairs[0]
        for other in stairs[1:]:
            self.assertAlmostEqual(other.x, first.x, places=6)
            self.assertAlmostEqual(other.y, first.y, places=6)
            self.assertAlmostEqual(other.w, first.w, places=6)
            self.assertAlmostEqual(other.h, first.h, places=6)

    def test_every_habitable_room_touches_an_exterior_wall(self):
        for floor in self.building.floors:
            for room in floor.rooms:
                if room.is_habitable:
                    self.assertTrue(
                        layout._facades_of(room, floor.footprint),
                        f"{floor.storey}階 {room.name} が外壁に接していない",
                    )

    def test_openings_are_generated(self):
        first = self.building.floors[0]
        self.assertTrue(first.openings)
        self.assertTrue(any(o.kind == "玄関ドア" for o in first.openings))
        for opening in first.openings:
            self.assertGreater(opening.width, 0)
            self.assertGreater(opening.height, 0)

    def test_entrance_door_faces_the_road(self):
        door = next(o for o in self.building.floors[0].openings if o.kind == "玄関ドア")
        self.assertEqual(door.facade, self.site.widest_road.direction)

    def test_habitable_rooms_have_windows(self):
        for floor in self.building.floors:
            for room in floor.rooms:
                if not room.is_habitable:
                    continue
                windows = [o for o in floor.openings if o.room == room.name]
                self.assertTrue(windows, f"{floor.storey}階 {room.name} に窓がない")

    def test_habitable_rooms_meet_the_daylight_requirement(self):
        """居室の窓は床面積の 1/7 を満たす（採光の法規判定が通る大きさで生成する）。"""
        for target in (None, 90.0, 115.7, 130.0):
            building = layout.generate(
                self.site, self.envelope, household_size=4, target_floor_area_m2=target
            )
            self.assert_daylight(building, target)

    def assert_daylight(self, building, target=None):
        for floor in building.floors:
            for room in floor.rooms:
                if not room.is_habitable:
                    continue
                area = sum(o.area_m2 for o in floor.openings if o.room == room.name)
                self.assertGreaterEqual(
                    area,
                    room.area_m2 / 7.0,
                    f"目標延床{target}: {floor.storey}階 {room.name} の採光が不足",
                )

    def test_openings_sit_on_the_grid_and_leave_a_wall_cell(self):
        """開口部はグリッド線上に置き、同じ面に 910mm 以上の壁を残す。"""
        from ai_land_design.geometry import bbox

        for floor in self.building.floors:
            x0, y0, _, _ = bbox(floor.footprint)
            for opening in floor.openings:
                origin = x0 if opening.facade.value in ("南", "北") else y0
                cells = (opening.position - origin) / layout.GRID_M
                self.assertAlmostEqual(cells, round(cells), places=6)
                width_cells = opening.width / layout.GRID_M
                self.assertAlmostEqual(width_cells, round(width_cells), places=6)
                room = next(r for r in floor.rooms if r.name == opening.room)
                _, span = layout._facade_span(room, opening.facade)
                self.assertLessEqual(
                    opening.width,
                    span - layout.GRID_M + 1e-6,
                    f"{floor.storey}階 {opening.room} の {opening.facade.value}面に壁が残らない",
                )

    def test_openings_stay_within_the_facade(self):
        from ai_land_design.geometry import bbox

        for floor in self.building.floors:
            x0, y0, x1, y1 = bbox(floor.footprint)
            for opening in floor.openings:
                if opening.facade.value in ("南", "北"):
                    low, high = x0, x1
                else:
                    low, high = y0, y1
                self.assertGreaterEqual(opening.position, low - 1e-6)
                self.assertLessEqual(opening.position + opening.width, high + 1e-6)

    def test_ldk_meets_minimum(self):
        ldk = next(r for f in self.building.floors for r in f.rooms if r.name == "LDK")
        self.assertGreaterEqual(ldk.area_m2, 16.0)

    def test_target_area_overrides_recommendation(self):
        building = layout.generate(self.site, self.envelope, target_floor_area_m2=90.0)
        # 910mm グリッドに丸めるため、目標を超えない範囲で最も近い面積になる
        self.assertLessEqual(building.total_floor_area_m2, 90.0 + 1e-6)
        self.assertGreater(building.total_floor_area_m2, 90.0 * 0.85)

    def test_tight_envelope_produces_single_storey(self):
        site = make_site(width=8.0, depth=9.0, bcr=0.5, far=0.6)
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope, household_size=2)
        self.assertLessEqual(
            building.total_floor_area_m2, envelope.max_floor_area_m2 + 1e-6
        )
        self.assertGreaterEqual(building.storeys, 1)

    def test_svg_contains_room_names(self):
        svg = layout.to_svg(self.site, self.building, storey=1)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("LDK", svg)
        self.assertIn("</svg>", svg)

    def test_svg_rejects_missing_storey(self):
        with self.assertRaises(ValueError):
            layout.to_svg(self.site, self.building, storey=9)

    def test_allocation_falls_back_to_proportional_when_area_is_tiny(self):
        specs = layout.program_for(1, 2)[1].rooms
        areas = layout._allocate_areas(specs, 10.0)
        self.assertAlmostEqual(sum(areas), 10.0, places=6)
        self.assertTrue(all(a > 0 for a in areas))

    def test_recommended_area_scales_with_household(self):
        self.assertLess(
            layout.recommended_floor_area_m2(2), layout.recommended_floor_area_m2(5)
        )


if __name__ == "__main__":
    unittest.main()
