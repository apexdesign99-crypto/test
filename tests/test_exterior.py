import unittest

from ai_land_design import exterior, feasibility, layout

from .helpers import make_site


class MassingTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, self.envelope, household_size=4)

    def test_mesh_has_geometry(self):
        mesh = exterior.build_massing(self.building)
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.faces), 0)

    def test_face_indices_are_valid(self):
        mesh = exterior.build_massing(self.building)
        for face in mesh.faces:
            self.assertGreaterEqual(len(face), 3)
            for index in face:
                self.assertGreaterEqual(index, 1)
                self.assertLessEqual(index, len(mesh.vertices))

    def test_obj_export_is_well_formed(self):
        obj = exterior.build_massing(self.building).to_obj("test")
        vertices = [l for l in obj.splitlines() if l.startswith("v ")]
        faces = [l for l in obj.splitlines() if l.startswith("f ")]
        self.assertGreater(len(vertices), 0)
        self.assertGreater(len(faces), 0)
        for line in vertices:
            self.assertEqual(len(line.split()), 4)

    def test_gable_roof_is_taller_than_flat(self):
        self.building.roof = "切妻"
        gable = exterior.total_height_m(self.building)
        self.building.roof = "陸屋根"
        flat = exterior.total_height_m(self.building)
        self.assertGreater(gable, flat)

    def test_height_covers_all_storeys(self):
        floors_height = sum(f.height_m for f in self.building.floors)
        self.assertGreaterEqual(exterior.total_height_m(self.building), floors_height)

    def test_svg_renders(self):
        svg = exterior.to_svg(self.building)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("polygon", svg)

    def test_empty_building_is_handled(self):
        self.building.floors = []
        self.assertEqual(exterior.total_height_m(self.building), 0.0)
        self.assertIn("<svg", exterior.to_svg(self.building))


if __name__ == "__main__":
    unittest.main()
