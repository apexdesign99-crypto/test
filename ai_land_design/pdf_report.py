"""確認申請図書の PDF 出力。

申請書の記載事項・法適合チェック・壁量計算書・図面一式を 1 冊の PDF にまとめる。
図面は `drawings.all_canvases()` が返す Canvas をそのままベクタで描くため、
SVG と同じ内容が拡大しても劣化せずに出る。

日本語フォントは環境から自動で探して**サブセット化して埋め込む**ので、
受け取った側の環境にフォントが無くても表示できる。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import application as application_module
from . import compliance as compliance_module
from . import drawings as drawings_module
from . import structure as structure_module
from .pdfkit import A3_LANDSCAPE, A4_PORTRAIT, FontError, PdfDocument
from .svgkit import Canvas

MARGIN = 42.0
INK = (0.11, 0.11, 0.09)
GRAY = (0.45, 0.44, 0.41)
ACCENT = (0.18, 0.44, 0.31)
DANGER = (0.69, 0.23, 0.18)
WARN = (0.69, 0.45, 0.11)
RULE = (0.72, 0.70, 0.65)


class Flow:
    """ページを跨ぐ縦組みのレイアウト。"""

    def __init__(self, document: PdfDocument, title: str, size=A4_PORTRAIT):
        self.document = document
        self.title = title
        self.size = size
        self.page = None
        self.y = 0.0
        self.new_page()

    @property
    def width(self) -> float:
        return self.size[0] - MARGIN * 2

    def new_page(self) -> None:
        self.page = self.document.add_page(self.size)
        self.y = self.size[1] - MARGIN
        self.page.text(MARGIN, self.y, self.title, 9, GRAY)
        self.page.line(
            MARGIN, self.y - 4, self.size[0] - MARGIN, self.y - 4, 0.5, RULE
        )
        self.y -= 22

    def space(self, amount: float = 10.0) -> None:
        self.y -= amount

    def ensure(self, needed: float) -> None:
        if self.y - needed < MARGIN:
            self.new_page()

    def heading(self, text: str, size: float = 13.0) -> None:
        self.ensure(size + 14)
        self.y -= size
        self.page.text(MARGIN, self.y, text, size, ACCENT)
        self.y -= 8

    def paragraph(self, text: str, size: float = 9.5, color=INK) -> None:
        for line in wrap(text, self.document, size, self.width):
            self.ensure(size + 4)
            self.y -= size + 2
            self.page.text(MARGIN, self.y, line, size, color)

    def table(
        self,
        rows: Sequence[Sequence[str]],
        widths: Sequence[float],
        header: bool = False,
        size: float = 8.5,
        colors: Optional[Sequence[Optional[Tuple[float, float, float]]]] = None,
    ) -> None:
        """表を描く。行が長い場合は折り返し、ページを跨ぐ。"""
        line_height = size + 3
        for index, row in enumerate(rows):
            cells = [
                wrap(str(value), self.document, size, width - 10)
                for value, width in zip(row, widths)
            ]
            height = max(len(cell) for cell in cells) * line_height + 6
            self.ensure(height)
            top = self.y
            x = MARGIN
            color = (colors[index] if colors and index < len(colors) else None) or INK
            for cell, width in zip(cells, widths):
                text_y = top - line_height
                for line in cell:
                    self.page.text(
                        x + 5, text_y, line, size,
                        GRAY if (header or index == 0 and header) else color,
                    )
                    text_y -= line_height
                x += width
            self.y = top - height
            self.page.line(MARGIN, self.y + 2, MARGIN + sum(widths), self.y + 2, 0.4, RULE)
        self.space(6)


def wrap(text: str, document: PdfDocument, size: float, max_width: float) -> List[str]:
    """指定幅に収まるよう文字列を折り返す。"""
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for char in str(text):
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if document.font.text_width(candidate, size) > max_width and current:
            lines.append(current)
            current = char
        else:
            current = candidate
    lines.append(current)
    return lines


def draw_canvas_page(document: PdfDocument, canvas: Canvas, size=A3_LANDSCAPE) -> None:
    """図面 1 枚を 1 ページに収める。"""
    page = document.add_page(size)
    available_w = size[0] - MARGIN * 2
    available_h = size[1] - MARGIN * 2 - 20
    scale = min(available_w / canvas.width_px, available_h / canvas.height_px)
    width = canvas.width_px * scale
    height = canvas.height_px * scale
    origin_x = (size[0] - width) / 2
    origin_y = (size[1] - height) / 2 - 6
    canvas.draw_on(page, origin_x, origin_y, scale)


def build(result, font_path: Optional[str] = None) -> bytes:
    """`ProjectResult` から申請図書の PDF を組み立てる。"""
    site = result.site
    building = result.building
    title = f"確認申請図書（下書き）　{site.address or site.site_id}"
    document = PdfDocument(font_path=font_path, title=title)

    # --- 表紙 ---
    cover = document.add_page(A4_PORTRAIT)
    cover.text(MARGIN, 760, "確認申請図書（下書き）", 22, INK)
    cover.line(MARGIN, 748, A4_PORTRAIT[0] - MARGIN, 748, 1.2, INK)
    cover.text(MARGIN, 716, site.address or site.site_id, 13, INK)

    info = result.options.application
    summary: List[Tuple[str, str]] = [
        ("用途", info.main_use),
        ("工事種別", info.work_type),
        ("用途地域", site.zoning.use_district.value),
        ("敷地面積", f"{site.area_m2:.2f} m²"),
    ]
    if building:
        summary += [
            ("構造・階数", f"{building.structure.value} 地上{building.storeys}階"),
            ("建築面積", f"{building.footprint_area_m2:.2f} m²"),
            ("延べ面積", f"{building.total_floor_area_m2:.2f} m²"),
            ("最高の高さ", f"{building.height_m:.2f} m"),
        ]
    summary += [
        ("建築主", info.owner.name or "（未記入）"),
        ("設計者", f"{info.designer.qualification} {info.designer.name}".strip() or "（未記入）"),
        ("作成日", date.today().isoformat()),
    ]
    y = 680
    for key, value in summary:
        cover.text(MARGIN, y, key, 10, GRAY)
        cover.text(MARGIN + 120, y, value, 11, INK)
        cover.line(MARGIN, y - 6, A4_PORTRAIT[0] - MARGIN, y - 6, 0.4, RULE)
        y -= 24

    note = (
        "本書は自動生成した下書きです。確認申請書は建築基準法施行規則 別記第二号様式に"
        "転記し、図面は建築士が確認・加筆してください。構造計算（壁量計算を除く）・"
        "省エネ計算・日影図・天空率は含まれていません。"
    )
    text_y = 180
    for line in wrap(note, document, 9, A4_PORTRAIT[0] - MARGIN * 2 - 20):
        cover.text(MARGIN + 10, text_y, line, 9, GRAY)
        text_y -= 13
    cover.rect(MARGIN, text_y - 4, A4_PORTRAIT[0] - MARGIN * 2, 180 - text_y + 20,
               stroke=RULE, stroke_width=0.6)

    if not building:
        flow = Flow(document, title)
        flow.heading("建築可能判定")
        flow.paragraph("建築可能判定で不可となったため、申請図書は作成できません。")
        for finding in result.envelope.findings:
            flow.paragraph(f"・[{finding.level}] {finding.message}")
        return document.output()

    # --- 申請書の記載事項 ---
    flow = Flow(document, title)
    for sheet, rows in application_module.sheets(
        site, result.envelope, building, info
    ).items():
        flow.heading(sheet)
        flow.table(rows, [flow.width * 0.32, flow.width * 0.68])

    # --- 法適合チェック ---
    if result.code_check:
        flow.heading("法適合チェック")
        report = result.code_check
        flow.paragraph(
            f"適合 {len(report.passed)} / 不適合 {len(report.failed)} / "
            f"要確認 {len(report.to_confirm)}"
        )
        widths = [flow.width * w for w in (0.07, 0.25, 0.16, 0.26, 0.26)]
        rows = [["判定", "項目", "根拠", "要求", "実績"]]
        colors: List[Optional[Tuple[float, float, float]]] = [GRAY]
        mark = {"適合": "○", "不適合": "×", "要確認": "△"}
        for item in report.items:
            rows.append([mark[item.result], item.name, item.law, item.required, item.actual])
            colors.append(
                DANGER if item.result == "不適合" else (WARN if item.result == "要確認" else INK)
            )
        flow.table(rows, widths, colors=colors)

    # --- 壁量計算 ---
    if result.wall_quantity:
        report = result.wall_quantity
        flow.heading("壁量計算（令46条4項）")
        flow.paragraph(
            f"係数表: {report.table.name}（適用 {report.table.effective}）／"
            f"屋根 {report.roof_weight}／壁倍率 外壁 {report.magnifications['外壁']}・"
            f"間仕切壁 {report.magnifications['間仕切壁']}"
        )
        flow.paragraph(
            f"壁量: {'充足' if report.quantity_ok else '不足'}"
            f"（最小充足率 {report.worst_ratio:.2f}）／"
            f"配置バランス: {'適合' if report.balance_ok else '不適合'}",
            color=INK if report.ok else DANGER,
        )
        if not report.verified:
            flow.paragraph(f"※ {report.table.note}", color=WARN)
        widths = [flow.width * w for w in (0.1, 0.2, 0.16, 0.14, 0.16, 0.12, 0.12)]
        rows = [["階", "方向", "必要壁量", "決定要因", "存在壁量", "充足率", "壁率比"]]
        axis_label = {"X": "桁行方向", "Y": "張り間方向"}
        for floor in report.floors:
            for direction in floor.directions:
                rows.append([
                    f"{floor.storey}階",
                    axis_label[direction.axis],
                    f"{direction.required_cm:.0f} cm",
                    direction.governing,
                    f"{direction.existing_cm:.0f} cm",
                    f"{direction.ratio:.2f}",
                    "—" if direction.quarter_ratio is None else f"{direction.quarter_ratio:.2f}",
                ])
        flow.table(rows, widths)

    # --- 図面 ---
    for canvas in drawings_module.all_canvases(site, building, result.envelope).values():
        draw_canvas_page(document, canvas)

    return document.output()
