import unittest
import uuid

from ai_land_design import feasibility, layout
from ai_land_design.bim import ifc

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

    def test_write_ifc_creates_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = ifc.write_ifc(Path(tmp) / "nested" / "model.ifc", self.site, self.building)
            self.assertTrue(path.exists())
            self.assertIn("IFC4", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
