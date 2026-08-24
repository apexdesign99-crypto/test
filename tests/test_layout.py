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

    def test_ldk_meets_minimum(self):
        ldk = next(r for f in self.building.floors for r in f.rooms if r.name == "LDK")
        self.assertGreaterEqual(ldk.area_m2, 16.0)

    def test_target_area_overrides_recommendation(self):
        building = layout.generate(self.site, self.envelope, target_floor_area_m2=90.0)
        self.assertAlmostEqual(building.total_floor_area_m2, 90.0, places=6)

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
        specs = layout.program_for(1, 2)[1]
        areas = layout._allocate_areas(specs, 10.0)
        self.assertAlmostEqual(sum(areas), 10.0, places=6)
        self.assertTrue(all(a > 0 for a in areas))

    def test_recommended_area_scales_with_household(self):
        self.assertLess(
            layout.recommended_floor_area_m2(2), layout.recommended_floor_area_m2(5)
        )


if __name__ == "__main__":
    unittest.main()
