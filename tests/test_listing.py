"""販売図面（マイソク）のテスト。"""

import unittest
import xml.etree.ElementTree as ET

from ai_land_design import listing, pipeline
from ai_land_design.listing import ListingInfo, format_price, specification_rows
from ai_land_design.pdfkit import find_japanese_font
from ai_land_design.svgkit import Canvas

from .helpers import make_site

HAS_FONT = find_japanese_font() is not None


class PriceFormatTest(unittest.TestCase):
    def test_man(self):
        self.assertEqual(format_price(95_000_000), "9,500万円")

    def test_oku(self):
        self.assertEqual(format_price(123_400_000), "1億2,340万円")

    def test_exact_oku(self):
        self.assertEqual(format_price(200_000_000), "2億円")

    def test_missing_price(self):
        self.assertEqual(format_price(None), "価格応談")
        self.assertEqual(format_price(0), "価格応談")


class SpecificationTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site(land_price_jpy=95_000_000, station_distance_m=640)
        self.result = pipeline.run(self.site)
        self.rows = dict(specification_rows(self.site, self.result, ListingInfo()))

    def test_core_fields(self):
        self.assertEqual(self.rows["用途地域"], self.site.zoning.use_district.value)
        self.assertIn("60％", self.rows["建蔽率／容積率"])
        self.assertIn(f"{self.site.area_m2:.2f}m²", self.rows["土地面積"])
        self.assertEqual(self.rows["土地価格"], "9,500万円")

    def test_spec_home_shows_the_sale_price(self):
        """分譲（建売）は土地建物一体の販売価格を載せる。"""
        result = pipeline.run(self.site, pipeline.Options(business_model="分譲住宅"))
        rows = dict(specification_rows(self.site, result, ListingInfo()))
        self.assertIn("販売価格（土地＋建物）", rows)
        self.assertNotIn("土地価格", rows)
        price, label = listing.sheet_price(self.site, result, ListingInfo())
        self.assertEqual(price, result.development.sale_price_jpy)
        self.assertEqual(label, "販売価格（土地＋建物）")

    def test_land_tsubo_price_is_calculated(self):
        self.assertIn("円", self.rows["土地坪単価"])

    def test_access_is_derived_from_station_distance(self):
        self.assertIn("640m", self.rows["交通"])

    def test_road_description(self):
        self.assertIn("南", self.rows["接道状況"])
        self.assertIn("6.0m", self.rows["接道状況"])

    def test_setback_road_is_disclosed(self):
        site = make_site(road_width=3.5, is_setback_road=True)
        rows = dict(specification_rows(site, pipeline.run(site), ListingInfo()))
        self.assertIn("セットバック", rows["接道状況"])

    def test_hazard_is_disclosed(self):
        from ai_land_design.models import Hazard

        site = make_site(hazard=Hazard(flood_depth_m=1.2, landslide_risk=True))
        rows = dict(specification_rows(site, pipeline.run(site), ListingInfo()))
        self.assertIn("浸水想定1.2m", rows["ハザード"])
        self.assertIn("土砂災害警戒区域", rows["ハザード"])

    def test_listing_info_overrides(self):
        info = ListingInfo(price_jpy=120_000_000, transaction_type="専任媒介", terrain="ひな壇")
        rows = dict(specification_rows(self.site, self.result, info))
        self.assertEqual(rows["価格"], "1億2,000万円")  # 明示指定なら名目は「価格」
        self.assertEqual(rows["取引態様"], "専任媒介")
        self.assertEqual(rows["地勢"], "ひな壇")


class SheetTest(unittest.TestCase):
    def setUp(self):
        self.site = make_site(land_price_jpy=95_000_000, station_distance_m=640)
        self.result = pipeline.run(self.site)
        self.info = ListingInfo(
            property_name="テスト区1丁目 売地",
            access="○○線 △△駅 徒歩8分",
            company="株式会社テスト不動産",
            license="国土交通大臣（1）第00000号",
        )

    def test_sheet_is_a4_sized(self):
        canvas = listing.sheet_canvas(self.site, self.result, self.info)
        self.assertAlmostEqual(canvas.width_px, listing.SHEET_WIDTH, places=6)
        self.assertLessEqual(canvas.height_px, listing.SHEET_HEIGHT + 1)

    def test_svg_is_valid_and_contains_the_key_facts(self):
        svg = listing.to_svg(self.site, self.result, self.info)
        ET.fromstring(svg)
        for expected in (
            "テスト区1丁目 売地", "9,500万円", "物件概要", "区画図", "参考プラン",
            "株式会社テスト不動産", "取引態様",
        ):
            self.assertIn(expected, svg, expected)

    def test_disclaimers_are_present(self):
        """参考プランであることと、実測に基づかないことを必ず明記する。"""
        svg = listing.to_svg(self.site, self.result, self.info)
        self.assertIn("自動生成した参考プラン", svg)
        self.assertIn("確定測量に基づくものではありません", svg)
        self.assertIn("建築条件は付いていません", svg)

    def test_plan_and_cost_are_included(self):
        svg = listing.to_svg(self.site, self.result, self.info)
        self.assertIn(self.result.building.ldk_type, svg)
        self.assertIn("総事業費", svg)

    def test_blocked_site_shows_no_plan(self):
        site = make_site(frontage=1.5)
        svg = listing.to_svg(site, pipeline.run(site), ListingInfo())
        self.assertIn("参考プランは作成していません", svg)
        ET.fromstring(svg)

    def test_embed_places_drawings_inside_the_sheet(self):
        canvas = listing.sheet_canvas(self.site, self.result, self.info)
        self.assertGreater(len(canvas.items), 100)  # 表・図面・注記がすべて入る

    @unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
    def test_pdf_is_one_a4_page(self):
        data = listing.to_pdf(self.site, self.result, self.info)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(data.count(b"/Type /Page /Parent"), 1)
        self.assertIn(b"595", data.split(b"MediaBox")[1][:40])

    def test_package_includes_the_sheet(self):
        files = pipeline.application_package(self.result, include_pdf=False)
        self.assertIn("販売図面.svg", files)

    @unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
    def test_package_includes_the_pdf_sheet(self):
        files = pipeline.application_package(self.result)
        self.assertIn("販売図面.pdf", files)
        self.assertIsInstance(files["販売図面.pdf"], bytes)


class CanvasEmbedTest(unittest.TestCase):
    def test_embed_scales_into_the_box(self):
        inner = Canvas(0, 0, 10, 10, scale=10)
        inner.rect(0, 0, 10, 10)
        outer = Canvas(0, 0, 100, 100, scale=1)
        scale = outer.embed(inner, 0, 0, box=(50, 50))
        self.assertLess(scale, 1.0)
        self.assertEqual(len(outer.items), 1)

    def test_embedded_items_stay_inside_the_box(self):
        inner = Canvas(0, 0, 10, 10, scale=10)
        inner.rect(0, 0, 10, 10)
        outer = Canvas(0, 0, 100, 100, scale=1)
        outer.embed(inner, 20, 30, box=(50, 40))
        points = [p for item in outer.items for p in item.points]
        self.assertTrue(all(20 <= x <= 70 + 1e-6 for x, _ in points))
        self.assertTrue(all(30 <= y <= 70 + 1e-6 for _, y in points))

    def test_centering(self):
        inner = Canvas(0, 0, 10, 10, scale=10)  # 正方形に近い
        inner.rect(0, 0, 10, 10)
        outer = Canvas(0, 0, 100, 100, scale=1)
        outer.embed(inner, 0, 0, box=(200, 100), center=True)
        xs = [x for item in outer.items for x, _ in item.points]
        self.assertGreater(min(xs), 0)  # 左端に張り付かず中央寄せされる


if __name__ == "__main__":
    unittest.main()
