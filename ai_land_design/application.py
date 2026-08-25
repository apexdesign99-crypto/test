"""確認申請書の記載事項シート。

建築基準法施行規則 別記第二号様式（確認申請書・建築物）の第一面〜第五面に
書き写す項目を、計画データから機械的に埋めて出力する。

**様式そのものではなく、様式に転記するためのデータシート**である。
申請にあたっては正規の様式（各行政庁・指定確認検査機関の配布様式）に
記入し、建築士が内容を確認する必要がある。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .compliance import ComplianceReport, daylight_check
from .drawings import ROOF_PITCH
from .models import Building, Envelope, FireZone, Site, Structure

BLANK = "（未記入）"


@dataclass
class Party:
    """申請に関わる者（建築主・設計者・工事監理者・工事施工者など）。"""

    name: str = ""
    address: str = ""
    phone: str = ""
    qualification: str = ""  # 一級建築士 など
    registration: str = ""  # 登録番号
    office: str = ""  # 建築士事務所名・登録番号

    def display(self, key: str) -> str:
        value = getattr(self, key, "")
        return value if value else BLANK

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApplicationInfo:
    """申請の当事者情報と工程。"""

    owner: Party = field(default_factory=Party)  # 建築主
    agent: Party = field(default_factory=Party)  # 代理者
    designer: Party = field(default_factory=Party)  # 設計者
    supervisor: Party = field(default_factory=Party)  # 工事監理者
    builder: Party = field(default_factory=Party)  # 工事施工者
    application_date: Optional[str] = None
    start_date: str = ""  # 着工予定
    completion_date: str = ""  # 完了予定
    work_type: str = "新築"
    main_use: str = "一戸建ての住宅"
    use_code: str = "08010"  # 用途コード（一戸建ての住宅）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner": self.owner.to_dict(),
            "agent": self.agent.to_dict(),
            "designer": self.designer.to_dict(),
            "supervisor": self.supervisor.to_dict(),
            "builder": self.builder.to_dict(),
            "application_date": self.application_date,
            "start_date": self.start_date,
            "completion_date": self.completion_date,
            "work_type": self.work_type,
            "main_use": self.main_use,
            "use_code": self.use_code,
        }


Row = Tuple[str, str]


def _ratio(numerator: float, denominator: float) -> str:
    return f"{numerator / denominator * 100:.2f}%" if denominator else "—"


def sheet_1(info: ApplicationInfo) -> List[Row]:
    """第一面：申請者・代理者・設計者・工事監理者・工事施工者。"""
    return [
        ("申請日", info.application_date or date.today().isoformat()),
        ("建築主 氏名", info.owner.display("name")),
        ("建築主 住所", info.owner.display("address")),
        ("代理者 氏名", info.agent.display("name")),
        ("代理者 建築士事務所", info.agent.display("office")),
        ("設計者 資格", info.designer.display("qualification")),
        ("設計者 氏名", info.designer.display("name")),
        ("設計者 建築士事務所", info.designer.display("office")),
        ("工事監理者 資格", info.supervisor.display("qualification")),
        ("工事監理者 氏名", info.supervisor.display("name")),
        ("工事施工者 氏名", info.builder.display("name")),
        ("工事施工者 営業所", info.builder.display("office")),
    ]


def sheet_2(info: ApplicationInfo) -> List[Row]:
    """第二面：建築主等の詳細。"""
    rows: List[Row] = []
    for label, party in (
        ("建築主", info.owner),
        ("代理者", info.agent),
        ("設計者", info.designer),
        ("工事監理者", info.supervisor),
        ("工事施工者", info.builder),
    ):
        rows.append((f"{label} 氏名", party.display("name")))
        rows.append((f"{label} 住所", party.display("address")))
        rows.append((f"{label} 電話番号", party.display("phone")))
        if party.qualification or party.registration:
            rows.append(
                (f"{label} 資格・登録番号", f"{party.display('qualification')} {party.display('registration')}")
            )
    return rows


def sheet_3(site: Site, envelope: Envelope, building: Building, info: ApplicationInfo) -> List[Row]:
    """第三面：建築物及びその敷地に関する事項。"""
    zoning = site.zoning
    road = site.widest_road
    site_area = envelope.effective_site_area_m2
    other_areas = []
    if zoning.shadow_regulation:
        other_areas.append("日影規制区域")
    if zoning.scenic_district:
        other_areas.append("風致地区等")
    if site.hazard.landslide_risk:
        other_areas.append("土砂災害警戒区域")

    return [
        ("地名地番", site.address or BLANK),
        ("住居表示", BLANK),
        ("都市計画区域等", "市街化区域"),
        ("用途地域", zoning.use_district.value),
        ("防火地域", zoning.fire_zone.value),
        ("その他の区域・地区", "、".join(other_areas) if other_areas else "指定なし"),
        (
            "敷地面積",
            f"{site.area_m2:.2f} m²"
            + (f"（うち道路後退 {envelope.setback_loss_m2:.2f} m² を除く：{site_area:.2f} m²）"
               if envelope.setback_loss_m2 else ""),
        ),
        (
            "道路の幅員 / 接する部分の長さ",
            f"{road.width_m:.1f} m / {road.frontage_m:.1f} m（{road.direction.value}側）" if road else BLANK,
        ),
        ("主要用途", f"{info.main_use}（用途コード {info.use_code}）"),
        ("工事種別", info.work_type),
        ("建築面積", f"{building.footprint_area_m2:.2f} m²"),
        ("建蔽率", f"{_ratio(building.footprint_area_m2, site_area)}"
                   f"（限度 {envelope.applied_coverage_ratio * 100:.0f}%）"),
        ("延べ面積", f"{building.total_floor_area_m2:.2f} m²"),
        ("容積率", f"{_ratio(building.total_floor_area_m2, site_area)}"
                   f"（限度 {envelope.applied_far * 100:.0f}%）"),
        ("建築物の数", "1"),
        ("建築物の階数", f"地上 {building.storeys} 階 / 地下 0 階"),
        ("最高の高さ", f"{building.height_m:.2f} m（限度 {envelope.max_height_m:.2f} m）"),
        ("軒の高さ", f"{sum(f.height_m for f in building.floors):.2f} m"),
        ("構造", f"{building.structure.value} 一部なし"),
        ("着工予定年月日", info.start_date or BLANK),
        ("完了予定年月日", info.completion_date or BLANK),
    ]


def sheet_4(site: Site, building: Building, info: ApplicationInfo) -> List[Row]:
    """第四面：建築物別概要（用途・構造・設備・居室の採光と換気）。"""
    rows: List[Row] = [
        ("番号", "1"),
        ("用途", info.main_use),
        ("構造", building.structure.value),
        ("階数", f"地上 {building.storeys} 階"),
        ("高さ", f"最高 {building.height_m:.2f} m / 軒 {sum(f.height_m for f in building.floors):.2f} m"),
        ("屋根", f"{building.roof}（勾配 {ROOF_PITCH * 10:.0f}寸相当）"),
        ("延べ面積", f"{building.total_floor_area_m2:.2f} m²"),
        ("居室の天井高さ", f"{building.floors[0].ceiling_height_m:.2f} m" if building.floors else BLANK),
    ]
    for floor in building.floors:
        for name, floor_area, window_area, ok in daylight_check(floor):
            rows.append(
                (
                    f"{floor.storey}階 {name} の採光・換気",
                    f"床 {floor_area:.2f} m² / 開口 {window_area:.2f} m²"
                    f"（採光 1/{floor_area / window_area:.1f}）" if window_area else "開口なし",
                )
            )
    rows += [
        ("換気設備", "機械換気（24時間換気・0.5回/h）"),
        ("昇降機", "なし"),
        ("非常用の進入口", "対象外（3階未満）" if building.storeys < 3 else "要検討"),
    ]
    return rows


def sheet_5(building: Building, info: ApplicationInfo) -> List[Row]:
    """第五面：建築物の階別概要。"""
    rows: List[Row] = []
    for floor in building.floors:
        rooms = "、".join(r.name for r in floor.rooms)
        rows.append((f"{floor.storey}階 用途", info.main_use))
        rows.append((f"{floor.storey}階 床面積", f"{floor.area_m2:.2f} m²"))
        rows.append((f"{floor.storey}階 室構成", rooms))
    rows.append(("合計 床面積", f"{building.total_floor_area_m2:.2f} m²"))
    return rows


def sheets(
    site: Site,
    envelope: Envelope,
    building: Building,
    info: Optional[ApplicationInfo] = None,
) -> Dict[str, List[Row]]:
    """第一面〜第五面のシートをまとめて返す。"""
    info = info or ApplicationInfo()
    if not building.floors:
        raise ValueError("建築可能な計画がないため申請書を作成できない")
    return {
        "第一面（申請者等）": sheet_1(info),
        "第二面（建築主等の詳細）": sheet_2(info),
        "第三面（建築物及びその敷地）": sheet_3(site, envelope, building, info),
        "第四面（建築物別概要）": sheet_4(site, building, info),
        "第五面（階別概要）": sheet_5(building, info),
    }


def to_html(
    site: Site,
    envelope: Envelope,
    building: Building,
    info: Optional[ApplicationInfo] = None,
    compliance_report: Optional[ComplianceReport] = None,
    drawings: Optional[Dict[str, str]] = None,
) -> str:
    """印刷用 HTML（A4）。ブラウザの印刷から PDF 化して提出用の下書きにする。"""
    info = info or ApplicationInfo()
    data = sheets(site, envelope, building, info)

    def table(rows: Sequence[Row]) -> str:
        body = "".join(
            f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in rows
        )
        return f"<table>{body}</table>"

    sections = "".join(
        f'<section class="sheet"><h2>{title}</h2>{table(rows)}</section>'
        for title, rows in data.items()
    )

    compliance_html = ""
    if compliance_report is not None:
        mark = {"適合": "✓", "不適合": "✗", "要確認": "△"}
        rows = "".join(
            f'<tr class="{item.result}"><td>{mark[item.result]}</td><td>{item.name}</td>'
            f"<td>{item.law}</td><td>{item.required}</td><td>{item.actual}</td></tr>"
            for item in compliance_report.items
        )
        compliance_html = (
            '<section class="sheet"><h2>法適合チェック</h2>'
            "<table class=\"check\"><thead><tr><th>判定</th><th>項目</th><th>根拠</th>"
            "<th>要求</th><th>実績</th></tr></thead><tbody>"
            f"{rows}</tbody></table></section>"
        )

    provenance_html = ""
    if site.provenance:
        rows = "".join(
            f"<tr><th>{record.get('field', '')}</th>"
            f"<td>{record.get('value', '')}<br><small>出典: {record.get('source', '')}"
            f"{'／' + record.get('note', '') if record.get('note') else ''}</small></td></tr>"
            for record in site.provenance
        )
        provenance_html = (
            '<section class="sheet"><h2>データ出典（自動取得した項目）</h2>'
            f"<table>{rows}</table>"
            "<p style=\"font-size:10px\">上記以外の項目は入力値または既定値です。"
            "確認申請にあたっては、都市計画情報・道路台帳・測量図で確認してください。</p></section>"
        )

    provenance_html = ""
    if site.provenance:
        rows = "".join(
            f"<tr><th>{record.get('field', '')}</th>"
            f"<td>{record.get('value', '')}<br><small>出典: {record.get('source', '')}"
            f"{'／' + record.get('note', '') if record.get('note') else ''}</small></td></tr>"
            for record in site.provenance
        )
        provenance_html = (
            '<section class="sheet"><h2>データ出典（自動取得した項目）</h2>'
            f"<table>{rows}</table>"
            '<p style="font-size:10px">上記以外の項目は入力値または既定値です。'
            "確認申請にあたっては、都市計画情報・道路台帳・測量図で確認してください。</p></section>"
        )

    drawing_html = ""
    if drawings:
        titles = {
            "site_plan.svg": "配置図",
            "section.svg": "断面図",
            "area_calculation.svg": "求積図",
        }
        blocks = []
        for name, svg in drawings.items():
            if name.startswith("plan_"):
                label = f"{name[5:-5].upper()} 平面図"
            elif name.startswith("elevation_"):
                label = f"{name[10:-4]}立面図"
            else:
                label = titles.get(name, name)
            blocks.append(f'<section class="sheet drawing"><h2>{label}</h2>{svg}</section>')
        drawing_html = "".join(blocks)

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>確認申請 記載事項シート — {site.address or site.site_id}</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  body {{ font-family: "Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;
         font-size: 11px; color: #1d1b17; margin: 0 auto; max-width: 900px; padding: 16px; }}
  h1 {{ font-size: 17px; border-bottom: 2px solid #1d1b17; padding-bottom: 6px; }}
  h2 {{ font-size: 13px; background: #f0eee9; padding: 5px 8px; margin: 18px 0 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
  th, td {{ border: 1px solid #b9b3a6; padding: 4px 7px; text-align: left; vertical-align: top; }}
  th {{ background: #fbfaf7; width: 32%; font-weight: 600; }}
  table.check th {{ width: auto; }}
  table.check td:first-child {{ text-align: center; width: 32px; font-weight: 700; }}
  tr.不適合 td {{ background: #fbeeec; }}
  tr.要確認 td {{ background: #fdf6e9; }}
  .sheet {{ page-break-inside: avoid; }}
  .drawing {{ page-break-before: always; text-align: center; }}
  .drawing svg {{ max-width: 100%; height: auto; }}
  .note {{ font-size: 10px; color: #6b665d; border: 1px solid #b9b3a6; padding: 8px; margin-top: 16px; }}
  @media print {{ .note {{ page-break-inside: avoid; }} }}
</style></head>
<body>
<h1>確認申請 記載事項シート（下書き）</h1>
<p>{site.address or site.site_id}　／　{info.main_use}　{building.structure.value}
{building.storeys}階建　延べ面積 {building.total_floor_area_m2:.2f} m²</p>
{sections}
{provenance_html}
{compliance_html}
{drawing_html}
<p class="note">本書は建築基準法施行規則 別記第二号様式に転記するためのデータシートであり、
様式そのものではありません。図面は自動生成の下書きです。確認申請にあたっては、
建築士による設計・確認と、特定行政庁または指定確認検査機関への提出が必要です。
構造計算・省エネ計算・日影図・天空率は本ツールの対象外です。</p>
</body></html>
"""


def to_markdown(
    site: Site,
    envelope: Envelope,
    building: Building,
    info: Optional[ApplicationInfo] = None,
) -> str:
    """記載事項シートの Markdown 版。"""
    data = sheets(site, envelope, building, info)
    lines = ["# 確認申請 記載事項シート（下書き）", ""]
    for title, rows in data.items():
        lines += [f"## {title}", "", "| 項目 | 記載内容 |", "| --- | --- |"]
        lines += [f"| {key} | {value} |" for key, value in rows]
        lines.append("")
    lines += [
        "---",
        "",
        "本書は別記第二号様式に転記するためのデータシートであり、様式そのものではありません。",
        "",
    ]
    return "\n".join(lines)
