"""PDF 出力（フォント埋め込み・図面のベクタ描画）のテスト。"""

import re
import unittest
import zlib
from pathlib import Path

from ai_land_design import drawings, feasibility, layout, pdf_report, pipeline
from ai_land_design.pdfkit import (
    A4_PORTRAIT,
    FontError,
    PdfDocument,
    TrueTypeFont,
    find_japanese_font,
)
from ai_land_design.svgkit import Canvas

from .helpers import make_site

FONT = find_japanese_font()
HAS_FONT = FONT is not None


def pdf_streams(data: bytes):
    """PDF 内の Flate ストリームを取り出して復号する。"""
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            yield zlib.decompress(match.group(1))
        except zlib.error:
            continue


@unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
class TrueTypeFontTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.font = TrueTypeFont(FONT)

    def test_metrics(self):
        self.assertGreater(self.font.units_per_em, 0)
        self.assertGreater(self.font.num_glyphs, 100)
        self.assertGreater(self.font.ascent, 0)

    def test_maps_japanese_and_latin(self):
        for char in "確認申請壁量A1":
            self.assertGreater(self.font.gid(char), 0, char)

    def test_unknown_char_maps_to_notdef(self):
        self.assertEqual(self.font.gid(""), 0)

    def test_widths(self):
        latin = self.font.width_1000(self.font.gid("A"))
        japanese = self.font.width_1000(self.font.gid("あ"))
        self.assertGreater(japanese, latin)  # 全角の方が広い
        self.assertAlmostEqual(japanese, 1000.0, delta=1.0)

    def test_text_width_scales_with_size(self):
        small = self.font.text_width("確認申請", 10)
        large = self.font.text_width("確認申請", 20)
        self.assertAlmostEqual(large, small * 2, places=6)

    def test_subset_is_far_smaller_than_the_original(self):
        gids = [self.font.gid(c) for c in "確認申請図書 壁量計算 ABC123"]
        subset = self.font.subset(gids)
        self.assertLess(len(subset), len(self.font.data) / 10)
        self.assertEqual(subset[:4], b"\x00\x01\x00\x00")  # sfnt ヘッダ

    def test_subset_keeps_required_tables(self):
        subset = TrueTypeFont(FONT).subset([1, 2, 3])
        for tag in (b"glyf", b"head", b"hhea", b"hmtx", b"loca", b"maxp"):
            self.assertIn(tag, subset[:400], tag)

    def test_rejects_non_font(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as handle:
            handle.write(b"not a font")
            path = handle.name
        with self.assertRaises(FontError):
            TrueTypeFont(path)
        Path(path).unlink()


@unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
class PdfDocumentTest(unittest.TestCase):
    def test_minimal_document(self):
        document = PdfDocument(title="テスト")
        page = document.add_page(A4_PORTRAIT)
        page.text(50, 700, "確認申請 記載事項", 14)
        data = document.output()
        self.assertTrue(data.startswith(b"%PDF-1.7"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"xref", data)
        self.assertIn(b"trailer", data)

    def test_font_is_embedded_as_a_composite_font(self):
        document = PdfDocument()
        document.add_page().text(50, 700, "日本語", 12)
        data = document.output()
        for marker in (b"/Type0", b"/CIDFontType2", b"/Identity-H", b"/FontFile2", b"/ToUnicode"):
            self.assertIn(marker, data, marker)

    def test_page_count(self):
        document = PdfDocument()
        for _ in range(3):
            document.add_page()
        self.assertEqual(document.output().count(b"/Type /Page /Parent"), 3)

    def test_japanese_title_is_encoded_as_utf16(self):
        data = PdfDocument(title="確認申請図書").output()
        self.assertIn(b"/Title <FEFF", data)

    def test_ascii_title_stays_literal(self):
        data = PdfDocument(title="Report").output()
        self.assertIn(b"/Title (Report)", data)

    def test_shapes_are_written_to_the_content_stream(self):
        document = PdfDocument()
        page = document.add_page()
        page.rect(10, 10, 100, 50, fill=(1, 0, 0))
        page.line(0, 0, 10, 10, 2)
        content = b"".join(pdf_streams(document.output()))
        self.assertIn(b" m ", content)  # パスの開始
        self.assertIn(b" l ", content)  # 直線
        self.assertIn(b"rg", content)  # 塗り色

    def test_rotated_text_uses_a_text_matrix(self):
        document = PdfDocument()
        document.add_page().text(100, 100, "寸法", 9, rotate=-90)
        content = b"".join(pdf_streams(document.output()))
        self.assertIn(b"Tm", content)

    def test_missing_font_path_raises_a_clear_error(self):
        with self.assertRaises(FontError) as caught:
            PdfDocument(font_path="/no/such/font.ttf")
        self.assertIn("フォントファイルがありません", str(caught.exception))


@unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
class CanvasToPdfTest(unittest.TestCase):
    def test_canvas_draws_onto_a_page(self):
        canvas = Canvas(0, 0, 10, 10, title="テスト図")
        canvas.rect(1, 1, 5, 5)
        canvas.text((3, 3), "室名", 10)
        canvas.dim_h(1, 6, 0.5)
        document = PdfDocument()
        page = document.add_page()
        canvas.draw_on(page, 40, 40, 0.5)
        content = b"".join(pdf_streams(document.output()))
        self.assertIn(b" m ", content)
        self.assertIn(b"Tj", content)

    def test_same_canvas_produces_svg_and_pdf(self):
        site = make_site()
        envelope = feasibility.evaluate(site)
        building = layout.generate(site, envelope)
        canvas = drawings.site_plan_canvas(site, building, envelope)
        self.assertTrue(canvas.render().startswith("<svg"))
        document = PdfDocument()
        canvas.draw_on(document.add_page(), 20, 20, 0.4)
        self.assertTrue(document.output().startswith(b"%PDF"))


@unittest.skipUnless(HAS_FONT, "日本語フォントが見つからない")
class ApplicationPdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = make_site(land_price_jpy=95_000_000)
        cls.result = pipeline.run(cls.site, pipeline.Options(roof_weight="重い"))
        cls.data = pdf_report.build(cls.result)

    def test_document_structure(self):
        self.assertTrue(self.data.startswith(b"%PDF"))
        pages = self.data.count(b"/Type /Page /Parent")
        # 表紙 + 申請書 + チェック + 壁量 + 図面（10枚）
        self.assertGreaterEqual(pages, 12)

    def test_contains_the_drawings(self):
        canvases = drawings.all_canvases(self.site, self.result.building, self.result.envelope)
        self.assertGreaterEqual(
            self.data.count(b"/Type /Page /Parent"), len(canvases) + 1
        )

    def test_text_is_extractable(self):
        """ToUnicode により本文が取り出せる（検索・コピーができる）。"""
        self.assertIn(b"/ToUnicode", self.data)

    def test_size_is_reasonable(self):
        self.assertLess(len(self.data), 3_000_000)  # フォントをサブセット化しているため小さい
        self.assertGreater(len(self.data), 20_000)

    def test_blocked_site_produces_a_short_document(self):
        blocked = pipeline.run(make_site(frontage=1.5))
        data = pdf_report.build(blocked)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertLessEqual(data.count(b"/Type /Page /Parent"), 3)

    def test_package_includes_the_pdf(self):
        files = pipeline.application_package(self.result)
        self.assertIn("申請図書.pdf", files)
        self.assertIsInstance(files["申請図書.pdf"], bytes)

    def test_package_can_skip_the_pdf(self):
        files = pipeline.application_package(self.result, include_pdf=False)
        self.assertNotIn("申請図書.pdf", files)

    def test_write_outputs_writes_binary(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            written = pipeline.write_outputs(self.result, tmp)
            pdf = next(p for p in written if p.suffix == ".pdf")
            self.assertGreater(pdf.stat().st_size, 20_000)
            self.assertEqual(pdf.read_bytes()[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()
