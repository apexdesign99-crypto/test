"""実施設計・確認申請の準備。

生成した建物案から、確認申請に必要な図書のチェックリストと、
申請概要（確認申請書 第三面・第四面に相当する項目）を組み立てる。
判定が必要な手続き（構造計算適合性判定・省エネ適合判定・中間検査など）も
規模と構造から自動で拾い出す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .models import Building, Envelope, Finding, Site, Structure


@dataclass
class ChecklistItem:
    name: str
    required: bool
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "required": self.required, "note": self.note}


def drawing_checklist(building: Building) -> List[ChecklistItem]:
    """確認申請に添付する設計図書。"""
    items = [
        ChecklistItem("付近見取図", True, "方位・道路・目標となる地物"),
        ChecklistItem("配置図", True, "縮尺・敷地境界線・道路幅員・建物位置・擁壁"),
        ChecklistItem("各階平面図", True, f"{building.storeys}階分（本ツールの間取り出力を基に作図）"),
        ChecklistItem("床面積求積図", True, "建築面積・各階床面積の求積"),
        ChecklistItem("敷地求積図", True, "敷地面積の求積"),
        ChecklistItem("立面図（2面以上）", True, "外観・高さ寸法・斜線制限の検討"),
        ChecklistItem("断面図（2面以上）", True, "最高高さ・軒高・階高"),
        ChecklistItem("矩計図", True, "各部詳細・断熱仕様"),
        ChecklistItem("構造図・構造計算書", True, "壁量計算または構造計算"),
        ChecklistItem("設備図（給排水・電気・換気）", True, "24時間換気の経路を明示"),
        ChecklistItem("省エネ計算書", True, "建築物省エネ法の適合性判定・届出"),
        ChecklistItem("シックハウス対策チェックシート", True, "内装仕上げ材の規制"),
    ]
    if building.structure is not Structure.WOOD:
        items.append(ChecklistItem("鉄骨・RC 各部詳細図", True, "接合部・配筋詳細"))
    return items


def procedures(site: Site, building: Building) -> List[ChecklistItem]:
    """規模・構造から必要な手続きを判定する。"""
    total = building.total_floor_area_m2
    storeys = building.storeys
    items: List[ChecklistItem] = [
        ChecklistItem("建築確認申請（法6条）", True, "着工前に確認済証の交付が必要"),
        ChecklistItem("完了検査申請（法7条）", True, "検査済証がなければ使用できない"),
    ]

    is_large_wood = building.structure is Structure.WOOD and (storeys >= 3 or total > 500)
    is_non_wood = building.structure is not Structure.WOOD and (storeys >= 2 or total > 200)
    items.append(
        ChecklistItem(
            "構造計算適合性判定",
            is_large_wood or is_non_wood,
            "許容応力度計算等が必要な規模に該当" if (is_large_wood or is_non_wood) else "対象外の見込み（仕様規定で対応）",
        )
    )
    items.append(
        ChecklistItem(
            "省エネ基準適合義務（建築物省エネ法）",
            True,
            "2025年4月以降、原則すべての新築建築物が適合義務の対象",
        )
    )
    items.append(
        ChecklistItem(
            "中間検査",
            storeys >= 3 or building.structure is not Structure.WOOD,
            "特定工程の指定は特定行政庁により異なるため要確認",
        )
    )
    items.append(
        ChecklistItem(
            "道路後退（42条2項）に伴う協議",
            any(r.width_m < 4.0 or r.is_setback_road for r in site.roads),
            "後退用地の分筆・寄付・管理について特定行政庁と協議",
        )
    )
    items.append(
        ChecklistItem(
            "日影規制の検討図書",
            site.zoning.shadow_regulation,
            "日影図による検証が必要" if site.zoning.shadow_regulation else "指定なし",
        )
    )
    items.append(
        ChecklistItem(
            "風致地区・地区計画等の許可申請",
            site.zoning.scenic_district,
            "意匠・高さ・外構に関する事前協議",
        )
    )
    items.append(
        ChecklistItem(
            "宅地造成・擁壁関係の許可",
            site.hazard.landslide_risk,
            "土砂災害警戒区域内のため構造検討・許可の要否を確認",
        )
    )
    return items


def application_summary(site: Site, envelope: Envelope, building: Building) -> Dict[str, Any]:
    """確認申請書に転記する主要数値（第三面・第四面相当）。"""
    site_area = site.area_m2
    building_area = building.footprint_area_m2
    total = building.total_floor_area_m2
    return {
        "地名地番": site.address,
        "用途地域": site.zoning.use_district.value,
        "防火地域": site.zoning.fire_zone.value,
        "敷地面積_m2": round(site_area, 2),
        "有効敷地面積_m2": round(envelope.effective_site_area_m2, 2),
        "建築面積_m2": round(building_area, 2),
        "建蔽率_実績": round(building_area / envelope.effective_site_area_m2, 4)
        if envelope.effective_site_area_m2
        else None,
        "建蔽率_限度": round(envelope.applied_coverage_ratio, 4),
        "延べ面積_m2": round(total, 2),
        "容積率_実績": round(total / envelope.effective_site_area_m2, 4)
        if envelope.effective_site_area_m2
        else None,
        "容積率_限度": round(envelope.applied_far, 4),
        "階数": building.storeys,
        "最高の高さ_m": round(building.height_m, 2),
        "高さの限度_m": round(envelope.max_height_m, 2),
        "主要構造": building.structure.value,
        "用途": "一戸建ての住宅",
        "工事種別": "新築",
    }


def compliance_check(envelope: Envelope, building: Building) -> List[Finding]:
    """生成した建物案が envelope の上限に収まっているかを検証する。"""
    findings: List[Finding] = []
    if building.footprint_area_m2 > envelope.max_building_area_m2 + 0.01:
        findings.append(
            Finding(
                "block",
                "OVER_BCR",
                f"建築面積{building.footprint_area_m2:.2f}m2 が限度{envelope.max_building_area_m2:.2f}m2 を超過。",
            )
        )
    if building.total_floor_area_m2 > envelope.max_floor_area_m2 + 0.01:
        findings.append(
            Finding(
                "block",
                "OVER_FAR",
                f"延べ面積{building.total_floor_area_m2:.2f}m2 が限度{envelope.max_floor_area_m2:.2f}m2 を超過。",
            )
        )
    if building.height_m > envelope.max_height_m + 0.01:
        findings.append(
            Finding(
                "block",
                "OVER_HEIGHT",
                f"高さ{building.height_m:.2f}m が限度{envelope.max_height_m:.2f}m を超過。",
            )
        )
    if not findings:
        findings.append(Finding("info", "COMPLIANT", "建蔽率・容積率・高さの限度内に収まっている。"))
    return findings


def to_markdown(site: Site, envelope: Envelope, building: Building) -> str:
    """申請準備用の Markdown を組み立てる。"""
    summary = application_summary(site, envelope, building)
    lines = ["# 確認申請 準備資料", "", "## 申請概要", "", "| 項目 | 値 |", "| --- | --- |"]
    for key, value in summary.items():
        if isinstance(value, float) and (key.endswith("実績") or key.endswith("限度")):
            value = f"{value * 100:.1f}%"
        lines.append(f"| {key} | {value} |")

    lines += ["", "## 必要な手続き", "", "| 手続き | 要否 | 備考 |", "| --- | --- | --- |"]
    for item in procedures(site, building):
        lines.append(f"| {item.name} | {'要' if item.required else '否'} | {item.note} |")

    lines += ["", "## 設計図書チェックリスト", ""]
    for item in drawing_checklist(building):
        lines.append(f"- [ ] {item.name} — {item.note}")

    lines += ["", "## 適合チェック", ""]
    for finding in compliance_check(envelope, building):
        mark = {"info": "OK", "warn": "注意", "block": "NG"}[finding.level]
        lines.append(f"- **{mark}** {finding.message}")

    lines += [
        "",
        "---",
        "",
        "本資料は自動生成された概算・準備資料です。確認申請にあたっては建築士による",
        "設計・確認と、特定行政庁または指定確認検査機関への事前相談が必要です。",
        "",
    ]
    return "\n".join(lines)
