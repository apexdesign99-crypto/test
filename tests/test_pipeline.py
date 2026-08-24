import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ai_land_design import pipeline
from ai_land_design.cli import main
from ai_land_design.models import Structure
from ai_land_design.sources.gis import LocalGisProvider

from .helpers import make_site

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


class PipelineTest(unittest.TestCase):
    def test_full_run_produces_all_stages(self):
        result = pipeline.run(make_site(), pipeline.Options(market_unit_price_per_tsubo=1_500_000))
        self.assertFalse(result.blocked)
        self.assertIsNotNone(result.building)
        self.assertIsNotNone(result.cost)
        self.assertTrue(result.envelope.buildable)
        self.assertTrue(all(f.level != "block" for f in result.compliance))

    def test_blocked_site_stops_after_feasibility(self):
        result = pipeline.run(make_site(frontage=1.5))
        self.assertTrue(result.blocked)
        self.assertIsNone(result.building)
        self.assertIsNone(result.cost)
        self.assertEqual(result.compliance[0].code, "NOT_BUILDABLE")

    def test_roof_switches_to_flat_when_height_is_tight(self):
        site = make_site(road_width=4.0, wall_setback_m=1.0, far=1.0)
        result = pipeline.run(site)
        self.assertLessEqual(result.building.height_m, result.envelope.max_height_m + 1e-6)

    def test_options_override_land_price(self):
        result = pipeline.run(make_site(), pipeline.Options(land_price_jpy=123_000_000))
        self.assertEqual(result.cost.land_price_jpy, 123_000_000)

    def test_structure_flows_into_cost(self):
        wood = pipeline.run(make_site(), pipeline.Options(structure=Structure.WOOD))
        rc = pipeline.run(make_site(), pipeline.Options(structure=Structure.RC))
        self.assertGreater(rc.cost.construction_total_jpy, wood.cost.construction_total_jpy)

    def test_to_dict_is_json_serializable(self):
        result = pipeline.run(make_site())
        text = json.dumps(result.to_dict(), ensure_ascii=False)
        data = json.loads(text)
        self.assertIn("summary", data)
        self.assertEqual(data["summary"]["storeys"], result.building.storeys)

    def test_markdown_report_has_all_sections(self):
        text = pipeline.to_markdown(pipeline.run(make_site()))
        for heading in (
            "## 1. AI 土地診断",
            "## 2. 建築可能判定",
            "## 3. AI 間取り",
            "## 4. 建築費",
            "## 5. 総事業費",
            "## 6. 適合チェック",
        ):
            self.assertIn(heading, text)

    def test_markdown_report_for_blocked_site(self):
        text = pipeline.to_markdown(pipeline.run(make_site(frontage=1.5)))
        self.assertIn("建築不可", text)
        self.assertNotIn("## 4. 建築費", text)

    def test_write_outputs(self):
        result = pipeline.run(make_site())
        with tempfile.TemporaryDirectory() as tmp:
            written = pipeline.write_outputs(result, tmp)
            names = {p.name for p in written}
            self.assertTrue(
                {"report.json", "report.md", "plan_1f.svg", "massing.obj", "model.ifc", "permit.md"}
                <= names
            )
            for path in written:
                self.assertGreater(path.stat().st_size, 0)

    def test_all_sample_sites_run(self):
        for site in LocalGisProvider(SAMPLES / "sites.json").all_sites():
            result = pipeline.run(site)
            self.assertIsNotNone(result.diagnosis)
            if not result.blocked:
                self.assertTrue(all(f.level != "block" for f in result.compliance))


def run_cli(argv):
    """標準出力を捨てて CLI を実行する。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


class CliTest(unittest.TestCase):
    def test_diagnose_json(self):
        code = run_cli(
            ["diagnose", "--input", str(SAMPLES / "sites.json"), "--site", "setagaya", "--json"]
        )
        self.assertEqual(code, 0)

    def test_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = run_cli(
                [
                    "run",
                    "--input",
                    str(SAMPLES / "sites.json"),
                    "--site",
                    "setagaya",
                    "--listings",
                    str(SAMPLES / "listings.json"),
                    "--market-area",
                    "世田谷",
                    "--out",
                    tmp,
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "model.ifc").exists())
            report = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            self.assertIsNotNone(report["options"]["market_unit_price_per_tsubo"])

    def test_run_returns_2_for_blocked_site(self):
        code = run_cli(["run", "--input", str(SAMPLES / "sites.json"), "--site", "hatajo_flag"])
        self.assertEqual(code, 2)

    def test_listings_command(self):
        self.assertEqual(
            run_cli(["listings", "--input", str(SAMPLES / "listings.json"), "--address", "世田谷"]), 0
        )
        self.assertEqual(
            run_cli(["listings", "--input", str(SAMPLES / "listings.json"), "--address", "沖縄"]), 1
        )

    def test_unknown_site_exits(self):
        with self.assertRaises(SystemExit):
            run_cli(["diagnose", "--input", str(SAMPLES / "sites.json"), "--site", "unknown"])

    def test_unknown_structure_exits(self):
        with self.assertRaises(SystemExit):
            run_cli(
                [
                    "run",
                    "--input",
                    str(SAMPLES / "sites.json"),
                    "--site",
                    "setagaya",
                    "--structure",
                    "木骨造",
                ]
            )


if __name__ == "__main__":
    unittest.main()
