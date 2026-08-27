"""販売図面（マイソク）の生成。

不動産会社が売地に付ける 1 枚ものの図面を、診断結果から組み立てる。
構成は日本の実務で一般的な形に合わせている。

    ┌──────────────────────────────────────────┐
    │ 物件名                            価格     │
    ├──────────────────┬───────────────────────┤
    │ 区画図（敷地・道路・寸法） │ 物件概要（表）        │
    ├──────────────────┴───────────────────────┤
    │ 参考プラン（各階平面図）      │ 外観イメージ       │
    ├──────────────────────────────────────────┤
    │ 建築プランの目安（構造・延床・建築費・総事業費）    │
    ├──────────────────────────────────────────┤
    │ 取引態様・注記・会社情報                       │
    └──────────────────────────────────────────┘

参考プランは自動生成した案であり、実際の建築計画・価格を保証するものではない。
その旨を図面上に明記する（誇大広告・不当表示を避けるため）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import drawings as drawings_module
from . import exterior as exterior_module
from . import layout as layout_module
from .models import Site
from .svgkit import ACCENT, GRAY, INK, MEDIUM, THIN, Canvas

#: A4 縦 [pt]
SHEET_WIDTH = 595.0
SHEET_HEIGHT = 842.0
PAD = 18.0

BAND = "#1d1b17"
PANEL_BORDER = "#b9b3a6"
LABEL_BG = "#f4f1ea"
BLANK = "―"


@dataclass
class ListingInfo:
    """販売図面に載せる商談上の情報（自動取得できない項目）。"""

    property_name: str = "売地"
    price_jpy: Optional[int] = None
    access: str = ""
    land_category: str = "宅地"  # 地目
    city_planning: str = "市街化区域"
    current_state: str = "更地"  # 現況
    delivery: str = "相談"  # 引渡
    transaction_type: str = "媒介"  # 取引態様
    building_condition: str = "無"  # 建築条件
    utilities: str = "公営水道・本下水・都市ガス"
    private_road_burden: str = "無"
    terrain: str = "平坦"
    note: str = ""
    company: str = ""
    license: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def format_price(value: Optional[int]) -> str:
    """価格の表示（1億2,000万円 / 9,500万円）。"""
    if not value:
        return "価格応談"
    oku, man = divmod(int(value), 100_000_000)
    man = man // 10_000
    if oku and man:
        return f"{oku}億{man:,}万円"
    if oku:
        return f"{oku}億円"
    return f"{man:,}万円"


def _panel(canvas: Canvas, x: float, y: float, w: float, h: float, title: str = "") -> float:
    """枠付きパネル。戻り値は中身の開始 y。"""
    canvas.rect_px(x, y, w, h, fill="#ffffff", stroke=PANEL_BORDER, width=THIN)
    if not title:
        return y
    canvas.rect_px(x, y, w, 16, fill=LABEL_BG, stroke=PANEL_BORDER, width=THIN)
    canvas.label_px(x + 6, y + 12, title, 9.5, color=ACCENT, weight="bold")
    return y + 16


def _spec_table(
    canvas: Canvas, x: float, y: float, w: float, rows: Sequence[Tuple[str, str]],
    row_height: float = 15.5, label_ratio: float = 0.32, size: float = 8.5
) -> float:
    """物件概要の表。戻り値は表の下端 y。"""
    for index, (label, value) in enumerate(rows):
        top = y + row_height * index
        canvas.rect_px(x, top, w * label_ratio, row_height, fill=LABEL_BG,
                       stroke=PANEL_BORDER, width=0.5)
        canvas.rect_px(x + w * label_ratio, top, w * (1 - label_ratio), row_height,
                       fill="#ffffff", stroke=PANEL_BORDER, width=0.5)
        canvas.label_px(x + 5, top + row_height - 4.5, label, size, color=GRAY)
        canvas.label_px(x + w * label_ratio + 5, top + row_height - 4.5, value or BLANK, size)
    return y + row_height * len(rows)


def specification_rows(site: Site, result, info: ListingInfo) -> List[Tuple[str, str]]:
    """物件概要表の項目。"""
    price = info.price_jpy or site.land_price_jpy
    tsubo_price = None
    if price and site.area_tsubo > 0:
        tsubo_price = int(round(price / site.area_tsubo))

    road = site.widest_road
    road_text = (
        f"{road.direction.value} 幅員{road.width_m:.1f}m ／ 接道{road.frontage_m:.1f}m"
        if road else BLANK
    )
    if road and (road.is_setback_road or road.width_m < 4.0):
        road_text += "（42条2項・要セットバック）"

    access = info.access
    if not access and site.station_distance_m:
        access = f"最寄駅まで約{site.station_distance_m}m（徒歩約{round(site.station_distance_m / 80)}分）"

    zoning = site.zoning
    rows: List[Tuple[str, str]] = [
        ("所在地", site.address or BLANK),
        ("交通", access),
        ("価格", format_price(price)),
        ("坪単価", f"{tsubo_price:,}円" if tsubo_price else BLANK),
        ("土地面積", f"{site.area_m2:.2f}m²（{site.area_tsubo:.2f}坪）"),
        ("私道負担", info.private_road_burden),
        ("用途地域", zoning.use_district.value),
        ("建蔽率／容積率", f"{zoning.building_coverage_ratio * 100:.0f}％ ／ "
                          f"{zoning.floor_area_ratio * 100:.0f}％"),
        ("接道状況", road_text),
        ("地目", info.land_category),
        ("都市計画", info.city_planning),
        ("防火指定", zoning.fire_zone.value),
        ("地勢", info.terrain),
        ("設備", info.utilities),
        ("現況／引渡", f"{info.current_state} ／ {info.delivery}"),
        ("建築条件", info.building_condition),
        ("取引態様", info.transaction_type),
    ]
    if site.hazard.flood_depth_m or site.hazard.landslide_risk:
        hazards = []
        if site.hazard.flood_depth_m:
            hazards.append(f"浸水想定{site.hazard.flood_depth_m:.1f}m")
        if site.hazard.landslide_risk:
            hazards.append("土砂災害警戒区域")
        rows.append(("ハザード", "／".join(hazards)))
    return rows


def sheet_canvas(site: Site, result, info: Optional[ListingInfo] = None) -> Canvas:
    """販売図面（A4 縦 1 枚）を組み立てる。"""
    info = info or ListingInfo()
    building = getattr(result, "building", None)
    canvas = Canvas(0, 0, SHEET_WIDTH, SHEET_HEIGHT - 34, scale=1.0, margin_m=0.0)

    # --- タイトル帯 ---
    canvas.rect_px(0, 0, SHEET_WIDTH, 46, fill=BAND, stroke=BAND)
    canvas.label_px(PAD, 20, info.property_name, 15, color="#ffffff", weight="bold")
    canvas.label_px(PAD, 37, site.address or "", 9, color="#cfc9bc")
    price = info.price_jpy or site.land_price_jpy
    canvas.label_px(SHEET_WIDTH - PAD, 30, format_price(price), 20, anchor="end",
                    color="#ffffff", weight="bold")

    top = 58.0
    left_w = SHEET_WIDTH * 0.46 - PAD
    right_x = PAD + left_w + 10
    right_w = SHEET_WIDTH - right_x - PAD

    # --- 区画図 ---
    plot_h = 250.0
    body = _panel(canvas, PAD, top, left_w, plot_h, "区画図")
    if building and building.floors:
        plot = drawings_module.site_plan_canvas(site, building, result.envelope)
    else:
        plot = _bare_site_canvas(site)
    canvas.embed(plot, PAD + 4, body + 2, box=(left_w - 8, plot_h - 22))

    # --- 物件概要 ---
    spec_body = _panel(canvas, right_x, top, right_w, plot_h, "物件概要")
    rows = specification_rows(site, result, info)
    _spec_table(canvas, right_x + 4, spec_body + 3, right_w - 8, rows,
                row_height=min(14.0, (plot_h - 24) / max(1, len(rows))))

    # --- 参考プラン ---
    plan_top = top + plot_h + 10
    plan_h = 230.0
    if building and building.floors:
        plan_w = SHEET_WIDTH * 0.62 - PAD
        body = _panel(canvas, PAD, plan_top, plan_w, plan_h,
                      f"参考プラン（{building.ldk_type}・{building.structure.value}"
                      f"{building.storeys}階建）")
        each = (plan_w - 12) / max(1, len(building.floors))
        for index, floor in enumerate(building.floors):
            plan = layout_module.plan_canvas(site, building, floor.storey)
            canvas.embed(plan, PAD + 6 + each * index, body + 2, box=(each - 4, plan_h - 24))

        view_x = PAD + plan_w + 10
        view_w = SHEET_WIDTH - view_x - PAD
        body = _panel(canvas, view_x, plan_top, view_w, plan_h, "外観イメージ")
        canvas.embed(exterior_module.massing_canvas(building), view_x + 4, body + 4,
                     box=(view_w - 8, plan_h - 30))
    else:
        body = _panel(canvas, PAD, plan_top, SHEET_WIDTH - PAD * 2, plan_h, "参考プラン")
        canvas.label_px(SHEET_WIDTH / 2, plan_top + plan_h / 2,
                        "建築可能判定で不可のため、参考プランは作成していません。",
                        10, anchor="middle", color=GRAY)

    # --- 建築プランの目安 ---
    cost_top = plan_top + plan_h + 10
    cost_h = 74.0
    body = _panel(canvas, PAD, cost_top, SHEET_WIDTH - PAD * 2, cost_h, "建築プランの目安（参考）")
    if building and result.cost:
        cost = result.cost
        items = [
            ("延床面積", f"{building.total_floor_area_m2:.2f}m²"),
            ("間取り", building.ldk_type),
            ("建築費（税込）", format_price(cost.construction_total_jpy)),
            ("諸費用", format_price(cost.other_total_jpy)),
            ("総事業費", format_price(cost.project_total_jpy)),
        ]
        column = (SHEET_WIDTH - PAD * 2 - 12) / len(items)
        for index, (label, value) in enumerate(items):
            x = PAD + 6 + column * index
            canvas.label_px(x, body + 18, label, 8.5, color=GRAY)
            canvas.label_px(x, body + 36, value, 12, weight="bold")
        canvas.label_px(
            PAD + 6, body + 52,
            "※ 建物は自動生成した参考プランです。建築条件は付いていません。"
            "価格・仕様は目安であり、実際の建築費・プランは設計内容により異なります。",
            7.5, color=GRAY,
        )
    else:
        canvas.label_px(PAD + 6, body + 20, "―", 10, color=GRAY)

    # --- フッタ ---
    footer_top = cost_top + cost_h + 10
    footer_h = 74.0
    canvas.rect_px(PAD, footer_top, SHEET_WIDTH - PAD * 2, footer_h,
                   fill="#fbfaf7", stroke=PANEL_BORDER, width=THIN)
    lines = [
        f"取引態様：{info.transaction_type}　／　建築条件：{info.building_condition}"
        f"　／　現況：{info.current_state}　／　引渡：{info.delivery}",
        info.note or "本図面は自動生成した参考資料です。記載内容は現況を優先し、"
        "行政・関係機関の指導により変更となる場合があります。",
        "掲載の区画図・参考プランは現地の実測・確定測量に基づくものではありません。",
        "用途地域・建蔽率・容積率等は公開データによるものです。最新の都市計画情報をご確認ください。",
    ]
    if info.company or info.license:
        lines.append(f"{info.company}　{info.license}".strip())
    for index, line in enumerate(lines):
        canvas.label_px(PAD + 6, footer_top + 14 + index * 12, line, 7.5,
                        color=INK if index == 0 else GRAY)

    # 用紙の下端に合わせて全体の高さを詰める
    canvas.max_y = footer_top + footer_h + PAD - 34
    return canvas


def _bare_site_canvas(site: Site) -> Canvas:
    """建物が無い場合の区画図（敷地と道路だけ）。"""
    from .geometry import bbox

    x0, y0, x1, y1 = bbox(site.polygon)
    canvas = Canvas(x0 - 3, y0 - 3, x1 + 3, y1 + 3, scale=14, margin_m=0.5,
                    title=f"{site.area_m2:.2f}m²")
    canvas.polygon(site.polygon, fill="#fbfaf7", stroke=INK, width=MEDIUM)
    for road in site.roads:
        canvas.text(
            ((x0 + x1) / 2, y0 - 1.5 if road.direction.value == "南" else y1 + 1.5),
            f"{road.direction.value}側道路 幅員{road.width_m:.1f}m", 9, color=GRAY,
        )
    canvas.dim_h(x0, x1, y0 - 2.4)
    canvas.dim_v(y0, y1, x1 + 2.0)
    return canvas


def to_svg(site: Site, result, info: Optional[ListingInfo] = None) -> str:
    """販売図面の SVG。"""
    return sheet_canvas(site, result, info).render()


def to_pdf(
    site: Site,
    result,
    info: Optional[ListingInfo] = None,
    font_path: Optional[str] = None,
) -> bytes:
    """販売図面の PDF（A4 縦 1 枚）。"""
    from .pdfkit import PdfDocument

    info = info or ListingInfo()
    canvas = sheet_canvas(site, result, info)
    document = PdfDocument(font_path=font_path, title=f"{info.property_name} 販売図面")
    page = document.add_page((SHEET_WIDTH, SHEET_HEIGHT))
    scale = min(SHEET_WIDTH / canvas.width_px, SHEET_HEIGHT / canvas.height_px)
    # 用紙の上端に合わせて配置する（キャンバスは上から組んでいるため）
    canvas.draw_on(page, 0, SHEET_HEIGHT - canvas.height_px * scale, scale)
    return document.output()
