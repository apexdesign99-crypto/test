import importlib.util
import tempfile
import unittest
import uuid
from pathlib import Path

from ai_land_design import feasibility, layout
from ai_land_design.bim import ifc

HAS_IFCOPENSHELL = importlib.util.find_spec("ifcopenshell") is not None

from .helpers import make_site


class GuidTest(unittest.TestCase):
    def test_guid_is_22_chars(self):
        guid = ifc.compress_guid(uuid.uuid4())
        self.assertEqual(len(guid), 22)
        self.assertTrue(all(c in ifc._GUID_CHARS for c in guid))

    def test_guid_is_deterministic(self):
        self.assertEqual(ifc.guid_for("a"), ifc.guid_for("a"))
        self.assertNotEqual(ifc.guid_for("a"), ifc.guid_for("b"))


class StringEncodingTest(unittest.TestCase):
    def test_ascii_passthrough(self):
        self.assertEqual(ifc.ifc_string("LDK"), "'LDK'")

    def test_quote_is_doubled(self):
        self.assertEqual(ifc.ifc_string("it's"), "'it''s'")

    def test_japanese_uses_x2_encoding(self):
        encoded = ifc.ifc_string("主寝室")
        self.assertTrue(encoded.startswith("'\\X2\\"))
        self.assertIn("\\X0\\", encoded)

    def test_real_numbers_always_have_decimal_point(self):
        self.assertIn(".", ifc.num(3))
        self.assertIn(".", ifc.num(0))


class IfcExportTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        envelope = feasibility.evaluate(self.site)
        self.building = layout.generate(self.site, envelope, household_size=4)
        self.text = ifc.to_ifc(self.site, self.building)

    def test_step_envelope(self):
        self.assertTrue(self.text.startswith("ISO-10303-21;"))
        self.assertTrue(self.text.rstrip().endswith("END-ISO-10303-21;"))
        self.assertIn("FILE_SCHEMA(('IFC4'));", self.text)
        self.assertEqual(self.text.count("DATA;"), 1)
        self.assertEqual(self.text.count("ENDSEC;"), 2)

    def test_spatial_hierarchy_present(self):
        for entity in ("IFCPROJECT(", "IFCSITE(", "IFCBUILDING(", "IFCBUILDINGSTOREY("):
            self.assertIn(entity, self.text, entity)

    def test_one_storey_entity_per_floor(self):
        self.assertEqual(
            self.text.count("IFCBUILDINGSTOREY("), len(self.building.floors)
        )

    def test_one_space_per_room(self):
        rooms = sum(len(f.rooms) for f in self.building.floors)
        self.assertEqual(self.text.count("IFCSPACE("), rooms)

    def test_slabs_and_walls(self):
        floors = len(self.building.floors)
        self.assertEqual(self.text.count("IFCSLAB("), floors)
        self.assertEqual(self.text.count("IFCWALLSTANDARDCASE("), floors * 4)

    def test_entity_ids_are_sequential(self):
        ids = [
            int(line.split("=")[0][1:])
            for line in self.text.splitlines()
            if line.startswith("#")
        ]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_every_reference_resolves(self):
        defined = set()
        used = set()
        for line in self.text.splitlines():
            if not line.startswith("#"):
                continue
            head, _, body = line.partition("=")
            defined.add(head.strip())
            for token in body.replace("(", " ").replace(")", " ").replace(",", " ").split():
                if token.startswith("#"):
                    used.add(token.rstrip(";"))
        self.assertTrue(used <= defined, f"未定義の参照: {sorted(used - defined)[:5]}")

    def test_output_is_deterministic(self):
        from datetime import datetime, timezone

        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = ifc.to_ifc(self.site, self.building, timestamp=stamp)
        second = ifc.to_ifc(self.site, self.building, timestamp=stamp)
        self.assertEqual(first, second)

    def test_openings_are_exported_as_windows_and_doors(self):
        openings = [o for f in self.building.floors for o in f.openings]
        windows = [o for o in openings if o.kind != "玄関ドア"]
        doors = [o for o in openings if o.kind == "玄関ドア"]
        self.assertEqual(self.text.count("IFCOPENINGELEMENT("), len(openings))
        self.assertEqual(self.text.count("IFCWINDOW("), len(windows))
        self.assertEqual(self.text.count("IFCDOOR("), len(doors))
        # 開口部は壁を抜き、建具が開口部を埋める
        self.assertEqual(self.text.count("IFCRELVOIDSELEMENT("), len(openings))
        self.assertEqual(self.text.count("IFCRELFILLSELEMENT("), len(openings))

    def test_property_sets_are_exported(self):
        for pset in ("Pset_WallCommon", "Pset_SpaceCommon", "Pset_WindowCommon"):
            self.assertIn(pset, self.text, pset)

    def test_application_property_set_needs_envelope(self):
        from ai_land_design import feasibility as feas

        envelope = feas.evaluate(self.site)
        with_envelope = ifc.to_ifc(self.site, self.building, envelope=envelope)
        self.assertIn("Pset_JP_ConfirmationApplication", with_envelope)
        # 日本語は \X2\ エンコードされるため、エンコード後の表記で確認する
        self.assertIn(ifc.ifc_string("建蔽率").strip("'"), with_envelope)
        self.assertIn(ifc.ifc_string("容積率の限度").strip("'"), with_envelope)

    def test_space_quantities(self):
        rooms = sum(len(f.rooms) for f in self.building.floors)
        self.assertEqual(self.text.count("IFCQUANTITYAREA("), rooms)
        self.assertEqual(self.text.count("IFCELEMENTQUANTITY("), rooms)

    def test_write_ifc_creates_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = ifc.write_ifc(Path(tmp) / "nested" / "model.ifc", self.site, self.building)
            self.assertTrue(path.exists())
            self.assertIn("IFC4", path.read_text(encoding="utf-8"))


@unittest.skipUnless(HAS_IFCOPENSHELL, "ifcopenshell が未インストール")
class IfcOpenShellValidationTest(unittest.TestCase):
    """実際の IFC パーサ（ifcopenshell）で読み込めることを確認する。"""

    @classmethod
    def setUpClass(cls):
        import ifcopenshell

        cls.site = make_site()
        envelope = feasibility.evaluate(cls.site)
        cls.building = layout.generate(cls.site, envelope, household_size=4)
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "model.ifc"
        path.write_text(ifc.to_ifc(cls.site, cls.building, envelope=envelope), encoding="utf-8")
        cls.model = ifcopenshell.open(str(path))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_schema_and_hierarchy(self):
        self.assertEqual(self.model.schema, "IFC4")
        self.assertEqual(len(self.model.by_type("IfcProject")), 1)
        self.assertEqual(len(self.model.by_type("IfcSite")), 1)
        self.assertEqual(len(self.model.by_type("IfcBuilding")), 1)
        self.assertEqual(
            len(self.model.by_type("IfcBuildingStorey")), len(self.building.floors)
        )

    def test_property_sets_are_readable(self):
        import ifcopenshell.util.element as util

        building = self.model.by_type("IfcBuilding")[0]
        psets = util.get_psets(building)
        self.assertIn("Pset_BuildingCommon", psets)
        application = psets["Pset_JP_ConfirmationApplication"]
        self.assertEqual(application["用途地域"], self.site.zoning.use_district.value)
        self.assertAlmostEqual(
            application["延べ面積"], self.building.total_floor_area_m2, places=2
        )

    def test_space_areas_match_the_plan(self):
        import ifcopenshell.util.element as util

        areas = {}
        for space in self.model.by_type("IfcSpace"):
            psets = util.get_psets(space)
            areas.setdefault(space.Name, psets["Pset_SpaceCommon"]["GrossPlannedArea"])
        ldk = next(r for f in self.building.floors for r in f.rooms if r.name == "LDK")
        self.assertAlmostEqual(areas["LDK"], ldk.area_m2, places=2)

    def test_windows_are_hosted_by_walls(self):
        for window in self.model.by_type("IfcWindow"):
            opening = window.FillsVoids[0].RelatingOpeningElement
            wall = opening.VoidsElements[0].RelatingBuildingElement
            self.assertTrue(wall.is_a("IfcWallStandardCase"))
            self.assertIn("外壁", wall.Name)

    def test_all_geometry_can_be_generated(self):
        import ifcopenshell.geom as geom

        settings = geom.settings()
        products = [p for p in self.model.by_type("IfcProduct") if p.Representation]
        self.assertGreater(len(products), 20)
        for product in products:
            geom.create_shape(settings, product)  # 例外が出ないこと


if __name__ == "__main__":
    unittest.main()
