"""パイプライン。

    不動産API / GIS・地図
            ↓
        AI土地診断
            ↓
        建築可能判定
            ↓
    AI間取り     建築費
            ↓
    3D外観      総事業費
            ↓
        BIM / IFC
            ↓
    実施設計・確認申請

各段の実装はモジュールに分かれており、この `run()` が受け渡しを担う。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import application as application_module
from . import compliance as compliance_module
from . import cost as cost_module
from . import diagnosis as diagnosis_module
from . import documents as documents_module
from . import drawings as drawings_module
from . import exterior as exterior_module
from . import feasibility as feasibility_module
from . import layout as layout_module
from .application import ApplicationInfo
from .bim import to_ifc
from .compliance import ComplianceReport
from .models import (
    Building,
    CostBreakdown,
    Diagnosis,
    Envelope,
    Finding,
    Site,
    Structure,
)


@dataclass
class Options:
    """事業条件の入力。"""

    household_size: int = 4
    structure: Structure = Structure.WOOD
    grade: str = "標準"
    floor_height_m: float = 2.9
    target_floor_area_m2: Optional[float] = None
    market_unit_price_per_tsubo: Optional[int] = None
    land_price_jpy: Optional[int] = None
    project_name: str = "AI LAND DESIGN"
    ceiling_height_m: float = 2.4
    application: ApplicationInfo = field(default_factory=ApplicationInfo)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "household_size": self.household_size,
            "structure": self.structure.value,
            "grade": self.grade,
            "floor_height_m": self.floor_height_m,
            "target_floor_area_m2": self.target_floor_area_m2,
            "market_unit_price_per_tsubo": self.market_unit_price_per_tsubo,
            "land_price_jpy": self.land_price_jpy,
            "project_name": self.project_name,
            "ceiling_height_m": self.ceiling_height_m,
            "application": self.application.to_dict(),
        }


@dataclass
class ProjectResult:
    """パイプライン全体の出力。"""

    site: Site
    options: Options
    diagnosis: Diagnosis
    envelope: Envelope
    building: Optional[Building]
    cost: Optional[CostBreakdown]
    compliance: List[Finding] = field(default_factory=list)
    code_check: Optional[ComplianceReport] = None

    @property
    def blocked(self) -> bool:
        return self.building is None or not self.envelope.buildable

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "site": self.site.to_dict(),
            "options": self.options.to_dict(),
            "diagnosis": self.diagnosis.to_dict(),
            "envelope": self.envelope.to_dict(),
            "building": self.building.to_dict() if self.building else None,
            "cost": self.cost.to_dict() if self.cost else None,
            "compliance": [f.to_dict() for f in self.compliance],
            "code_check": self.code_check.to_dict() if self.code_check else None,
        }
        if self.building and self.cost:
            data["summary"] = {
                "rank": self.diagnosis.rank,
                "score": round(self.diagnosis.total_score, 1),
                "plan": self.building.ldk_type,
                "storeys": self.building.storeys,
                "total_floor_area_m2": round(self.building.total_floor_area_m2, 2),
                "construction_total_jpy": self.cost.construction_total_jpy,
                "construction_unit_price_per_tsubo": cost_module.unit_cost_per_tsubo(
                    self.cost, self.building.total_floor_area_m2
                ),
                "project_total_jpy": self.cost.project_total_jpy,
            }
        return data


def run(site: Site, options: Optional[Options] = None) -> ProjectResult:
    """敷地から総事業費・BIM 前段までを一気通貫で算出する。"""
    options = options or Options()
    if options.land_price_jpy is not None:
        site.land_price_jpy = options.land_price_jpy

    diagnosis = diagnosis_module.diagnose(site, options.market_unit_price_per_tsubo)
    envelope = feasibility_module.evaluate(site, floor_height_m=options.floor_height_m)

    if not envelope.buildable:
        return ProjectResult(
            site=site,
            options=options,
            diagnosis=diagnosis,
            envelope=envelope,
            building=None,
            cost=None,
            compliance=[
                Finding("block", "NOT_BUILDABLE", "建築可能判定で不可となったため以降の工程を実行しない。")
            ],
        )

    building = layout_module.generate(
        site,
        envelope,
        household_size=options.household_size,
        structure=options.structure,
        floor_height_m=options.floor_height_m,
        target_floor_area_m2=options.target_floor_area_m2,
        ceiling_height_m=options.ceiling_height_m,
    )

    # 屋根形状：切妻の棟が高さ制限を超える場合は陸屋根に切り替える。
    if exterior_module.total_height_m(building) > envelope.max_height_m:
        building.roof = "陸屋根"
    building.height_m = min(exterior_module.total_height_m(building), envelope.max_height_m)

    breakdown = cost_module.estimate(
        site,
        building,
        grade=options.grade,
        land_price_jpy=options.land_price_jpy,
    )
    compliance = documents_module.compliance_check(envelope, building)
    code_check = compliance_module.evaluate(site, envelope, building)

    return ProjectResult(
        site=site,
        options=options,
        diagnosis=diagnosis,
        envelope=envelope,
        building=building,
        cost=breakdown,
        compliance=compliance,
        code_check=code_check,
    )


def to_markdown(result: ProjectResult) -> str:
    """人が読むレポート（Markdown）。"""
    site = result.site
    lines = [
        f"# AI LAND DESIGN 事業性レポート — {site.address or site.site_id}",
        "",
        f"- 敷地面積: {site.area_m2:.2f} m2（{site.area_tsubo:.2f} 坪）",
        f"- 用途地域: {site.zoning.use_district.value} / 建蔽率 "
        f"{site.zoning.building_coverage_ratio * 100:.0f}% / 容積率 "
        f"{site.zoning.floor_area_ratio * 100:.0f}%",
        "",
        "## 1. AI 土地診断",
        "",
        f"**総合スコア {result.diagnosis.total_score:.1f} 点（ランク {result.diagnosis.rank}）**",
        "",
        "| 評価軸 | スコア | 重み | コメント |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in result.diagnosis.items:
        lines.append(f"| {item.name} | {item.score:.1f} | {item.weight:.2f} | {item.comment} |")

    if result.diagnosis.findings:
        lines += ["", "指摘事項:", ""]
        for finding in result.diagnosis.findings:
            lines.append(f"- [{finding.level}] {finding.message}")

    env = result.envelope
    lines += [
        "",
        "## 2. 建築可能判定",
        "",
        f"- 判定: **{'建築可' if env.buildable else '建築不可'}**",
        f"- 有効敷地面積: {env.effective_site_area_m2:.2f} m2"
        + (f"（セットバック控除 {env.setback_loss_m2:.2f} m2）" if env.setback_loss_m2 else ""),
        f"- 建蔽率（緩和後）: {env.applied_coverage_ratio * 100:.0f}% → 建築面積上限 "
        f"{env.max_building_area_m2:.2f} m2",
        f"- 容積率（道路幅員考慮）: {env.applied_far * 100:.0f}% → 延べ面積上限 "
        f"{env.max_floor_area_m2:.2f} m2",
        f"- 高さ上限: {env.max_height_m:.2f} m / 想定階数: {env.max_storeys} 階",
        "",
        "| 高さ制限 | 上限 | 根拠 |",
        "| --- | ---: | --- |",
    ]
    for limit in env.height_limits:
        value = "適用外" if limit.limit_m >= 999 else f"{limit.limit_m:.2f} m"
        lines.append(f"| {limit.name} | {value} | {limit.detail} |")
    if env.findings:
        lines += ["", "指摘事項:", ""]
        for finding in env.findings:
            lines.append(f"- [{finding.level}] {finding.message}")

    if not result.building or not result.cost:
        lines += ["", "建築可能判定で不可となったため、以降の工程は算出していない。", ""]
        return "\n".join(lines)

    building = result.building
    lines += [
        "",
        "## 3. AI 間取り",
        "",
        f"- {building.structure.value} {building.storeys}階建て / {building.ldk_type} / "
        f"延べ面積 {building.total_floor_area_m2:.2f} m2",
        f"- 建築面積 {building.footprint_area_m2:.2f} m2 / 最高高さ {building.height_m:.2f} m "
        f"/ 屋根 {building.roof}",
        "",
    ]
    for floor in building.floors:
        lines += [
            f"### {floor.storey}階（{floor.area_m2:.2f} m2）",
            "",
            "| 室名 | 面積 | 帖数 | 寸法 |",
            "| --- | ---: | ---: | --- |",
        ]
        for room in floor.rooms:
            lines.append(
                f"| {room.name} | {room.area_m2:.2f} m2 | {room.jo:.1f} 帖 | "
                f"{room.w:.2f} × {room.h:.2f} m |"
            )
        lines.append("")

    breakdown = result.cost
    lines += [
        "## 4. 建築費",
        "",
        "| 項目 | 金額 | 備考 |",
        "| --- | ---: | --- |",
    ]
    for item in breakdown.construction_items:
        lines.append(f"| {item.name} | {item.amount_jpy:,} 円 | {item.note} |")
    lines += [
        f"| 消費税（{breakdown.tax_rate:.0%}） | {breakdown.construction_tax_jpy:,} 円 | |",
        f"| **建築費 合計** | **{breakdown.construction_total_jpy:,} 円** | "
        f"坪単価 {cost_module.unit_cost_per_tsubo(breakdown, building.total_floor_area_m2):,} 円 |",
        "",
        "## 5. 総事業費",
        "",
        "| 項目 | 金額 | 備考 |",
        "| --- | ---: | --- |",
        f"| 土地取得費 | {breakdown.land_price_jpy:,} 円 | |",
        f"| 建築費（税込） | {breakdown.construction_total_jpy:,} 円 | |",
    ]
    for item in breakdown.other_items:
        lines.append(f"| {item.name} | {item.amount_jpy:,} 円 | {item.note} |")
    lines += [
        f"| **総事業費** | **{breakdown.project_total_jpy:,} 円** | |",
        "",
        "## 6. 適合チェック",
        "",
    ]
    for finding in result.compliance:
        mark = {"info": "OK", "warn": "注意", "block": "NG"}[finding.level]
        lines.append(f"- **{mark}** {finding.message}")
    lines += [
        "",
        "---",
        "",
        "本レポートは公開情報と概算モデルによる自動生成物であり、法適合の最終判断・",
        "見積の確定には建築士および施工者による精査が必要です。",
        "",
    ]
    return "\n".join(lines)


def application_package(result: ProjectResult) -> Dict[str, str]:
    """確認申請用の成果物一式（ファイル名 → 内容）。

    図面（配置図・平面図・立面図4面・断面図・求積図）、IFC、申請書の記載事項、
    法適合チェック、事業性レポートをまとめて返す。ZIP 化や書き出しは呼び出し側で行う。
    """
    files: Dict[str, str] = {"report.md": to_markdown(result)}
    files["report.json"] = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    if not (result.building and result.building.floors):
        return files

    site, envelope, building = result.site, result.envelope, result.building
    drawings = drawings_module.all_drawings(site, building, envelope)
    for name, svg in drawings.items():
        files[f"図面/{name}"] = svg

    files["model.ifc"] = to_ifc(
        site, building, project_name=result.options.project_name, envelope=envelope
    )
    files["massing.obj"] = exterior_module.build_massing(building).to_obj(site.site_id)
    files["exterior.svg"] = exterior_module.to_svg(building)

    info = result.options.application
    files["申請書_記載事項.md"] = application_module.to_markdown(site, envelope, building, info)
    files["申請書_記載事項.html"] = application_module.to_html(
        site, envelope, building, info, result.code_check, drawings
    )
    if result.code_check:
        files["法適合チェック.md"] = compliance_module.to_markdown(result.code_check)
        files["法適合チェック.json"] = json.dumps(
            result.code_check.to_dict(), ensure_ascii=False, indent=2
        )
    files["確認申請_図書チェックリスト.md"] = documents_module.to_markdown(site, envelope, building)
    return files


def write_outputs(result: ProjectResult, out_dir: str | Path) -> List[Path]:
    """申請パッケージ（図面・IFC・申請書・チェック・レポート）を書き出す。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for name, content in application_package(result).items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
