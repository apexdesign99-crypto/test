"""建築確認申請前の法適合チェック。

生成した建物案を建築基準法の主要規定に照らし、項目ごとに
「適合 / 不適合 / 要確認」と根拠条文・数値を返す。確認申請の事前チェック
（審査機関に出す前の自主チェック）に相当する。

対象は集団規定（用途・接道・建蔽率・容積率・高さ・斜線・外壁後退・防火）と
単体規定（採光・換気・天井高・階段・シックハウス）。構造計算・省エネ計算・
日影図・天空率は本ツールの範囲外のため「要確認」として明示する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .feasibility import height_limits
from .geometry import bbox
from .models import (
    Building,
    Direction,
    Envelope,
    FireZone,
    Floor,
    Site,
    Structure,
    UseDistrict,
)

PASS = "適合"
FAIL = "不適合"
CHECK = "要確認"

#: 居室の採光に必要な開口面積の割合（法28条1項・令19条：住宅は 1/7）
DAYLIGHT_RATIO = 1 / 7
#: 居室の換気に必要な開口面積の割合（法28条2項：1/20）
VENTILATION_RATIO = 1 / 20
#: 居室の天井高さの下限（令21条）
MIN_CEILING_M = 2.1
#: 階段の寸法（令23条：住宅の場合）
MAX_RISER_M = 0.23
MIN_TREAD_M = 0.15
MIN_STAIR_WIDTH_M = 0.75
#: 壁厚の想定（内法寸法の算定用）
WALL_THICKNESS_M = 0.15


@dataclass
class CheckItem:
    """チェック1項目。"""

    category: str  # 集団規定 / 単体規定 / 手続き
    name: str
    law: str  # 根拠条文
    required: str  # 要求値
    actual: str  # 実績値
    result: str  # 適合 / 不適合 / 要確認
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "law": self.law,
            "required": self.required,
            "actual": self.actual,
            "result": self.result,
            "note": self.note,
        }


@dataclass
class ComplianceReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def failed(self) -> List[CheckItem]:
        return [i for i in self.items if i.result == FAIL]

    @property
    def to_confirm(self) -> List[CheckItem]:
        return [i for i in self.items if i.result == CHECK]

    @property
    def passed(self) -> List[CheckItem]:
        return [i for i in self.items if i.result == PASS]

    @property
    def ready(self) -> bool:
        """不適合がなく、申請図書の作成に進める状態か。"""
        return not self.failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "summary": {
                "適合": len(self.passed),
                "不適合": len(self.failed),
                "要確認": len(self.to_confirm),
            },
            "items": [i.to_dict() for i in self.items],
        }


def _verdict(ok: bool) -> str:
    return PASS if ok else FAIL


def actual_setbacks(site: Site, building: Building) -> Dict[Direction, float]:
    """建物外壁から敷地境界までの実際の距離 [m]（方位別）。"""
    if not building.floors:
        return {}
    sx0, sy0, sx1, sy1 = bbox(site.polygon)
    bx0, by0, bx1, by1 = bbox(building.floors[0].footprint)
    return {
        Direction.S: by0 - sy0,
        Direction.N: sy1 - by1,
        Direction.W: bx0 - sx0,
        Direction.E: sx1 - bx1,
    }


def daylight_check(floor: Floor) -> List[Tuple[str, float, float, bool]]:
    """各居室の (室名, 床面積, 有効開口面積, 採光の可否)。"""
    results = []
    for room in floor.rooms:
        if not room.is_habitable:
            continue
        window_area = sum(
            o.area_m2 for o in floor.openings if o.room == room.name and o.kind != "玄関ドア"
        )
        results.append(
            (room.name, room.area_m2, window_area, window_area >= room.area_m2 * DAYLIGHT_RATIO)
        )
    return results


def stair_dimensions(building: Building) -> Optional[Tuple[float, float, float, int]]:
    """階段の (幅, 蹴上, 踏面, 段数)。直階段として算定する。"""
    if len(building.floors) < 2:
        return None
    stair = building.floors[0].room("階段")
    if stair is None:
        return None
    inner_w = min(stair.w, stair.h) - WALL_THICKNESS_M
    run = max(stair.w, stair.h) - WALL_THICKNESS_M
    rise_total = building.floors[0].height_m
    steps = max(2, math.ceil(rise_total / MAX_RISER_M))
    riser = rise_total / steps
    tread = run / max(1, steps - 1)
    return inner_w, riser, tread, steps


def evaluate(site: Site, envelope: Envelope, building: Building) -> ComplianceReport:
    """建物案の法適合をチェックする。"""
    items: List[CheckItem] = []
    zoning = site.zoning
    use = zoning.use_district

    # ---------- 集団規定 ----------
    items.append(
        CheckItem(
            "集団規定",
            "用途制限",
            "法48条・別表第2",
            "一戸建ての住宅が建築可能な用途地域",
            use.value,
            _verdict(use.allows_dwelling),
        )
    )

    road = site.widest_road
    frontage = max((r.frontage_m for r in site.roads if r.is_legal_road), default=0.0)
    width = max((r.width_m for r in site.roads if r.is_legal_road), default=0.0)
    effective_width = 4.0 if 0 < width < 4.0 else width
    items.append(
        CheckItem(
            "集団規定",
            "接道義務",
            "法43条1項",
            "幅員4m以上の道路に2m以上接すること",
            f"幅員{width:.1f}m（後退後{effective_width:.1f}m）に{frontage:.1f}m接道",
            _verdict(effective_width >= 4.0 and frontage >= 2.0),
        )
    )

    if not building.floors:
        items.append(
            CheckItem("集団規定", "建築可能性", "—", "建築可能な計画", "計画なし", FAIL)
        )
        return ComplianceReport(items)

    site_area = envelope.effective_site_area_m2
    building_area = building.footprint_area_m2
    total_area = building.total_floor_area_m2
    bcr_actual = building_area / site_area if site_area else 0.0
    far_actual = total_area / site_area if site_area else 0.0

    items.append(
        CheckItem(
            "集団規定",
            "建蔽率",
            "法53条",
            f"{envelope.applied_coverage_ratio * 100:.0f}% 以下"
            f"（建築面積 {envelope.max_building_area_m2:.2f}m²以下）",
            f"{bcr_actual * 100:.1f}%（建築面積 {building_area:.2f}m²）",
            _verdict(building_area <= envelope.max_building_area_m2 + 1e-6),
        )
    )
    items.append(
        CheckItem(
            "集団規定",
            "容積率",
            "法52条",
            f"{envelope.applied_far * 100:.0f}% 以下"
            f"（延べ面積 {envelope.max_floor_area_m2:.2f}m²以下）",
            f"{far_actual * 100:.1f}%（延べ面積 {total_area:.2f}m²）",
            _verdict(total_area <= envelope.max_floor_area_m2 + 1e-6),
            "地下室・車庫の緩和は未算入" if far_actual > 0 else "",
        )
    )

    # 実際の外壁後退距離で斜線制限を再計算する
    setbacks = actual_setbacks(site, building)
    road_setback = setbacks.get(road.direction, 0.5) if road else 0.5
    depth = max(building.floors[0].footprint[2][1] - building.floors[0].footprint[0][1], 1.0)
    limits = height_limits(site, wall_setback_m=max(road_setback, 0.3), building_depth_m=depth)
    for limit in limits:
        if limit.limit_m >= 999:
            items.append(
                CheckItem(
                    "集団規定", limit.name, "法56条", "適用外", limit.detail, PASS
                )
            )
            continue
        law = {
            "絶対高さ制限": "法55条",
            "道路斜線制限": "法56条1項1号",
            "隣地斜線制限": "法56条1項2号",
            "北側斜線制限": "法56条1項3号",
        }.get(limit.name, "法56条")
        items.append(
            CheckItem(
                "集団規定",
                limit.name,
                law,
                f"{limit.limit_m:.2f}m 以下",
                f"最高の高さ {building.height_m:.2f}m",
                _verdict(building.height_m <= limit.limit_m + 1e-6),
                limit.detail,
            )
        )

    if zoning.wall_setback_m > 0:
        worst = min(setbacks.values()) if setbacks else 0.0
        items.append(
            CheckItem(
                "集団規定",
                "外壁の後退距離",
                "法54条",
                f"{zoning.wall_setback_m:.1f}m 以上",
                f"最小 {worst:.2f}m",
                _verdict(worst >= zoning.wall_setback_m - 1e-6),
            )
        )

    if zoning.shadow_regulation:
        items.append(
            CheckItem(
                "集団規定", "日影規制", "法56条の2", "規制時間内であること",
                "日影図による検証が必要", CHECK, "本ツールでは日影計算を行っていない",
            )
        )

    if zoning.fire_zone is not FireZone.NONE:
        needs_fireproof = zoning.fire_zone is FireZone.FIRE and (
            building.storeys >= 3 or total_area > 100
        )
        needs_quasi = zoning.fire_zone is FireZone.QUASI and building.storeys >= 3
        if needs_fireproof:
            required = "耐火建築物"
        elif needs_quasi or zoning.fire_zone is FireZone.FIRE:
            required = "準耐火建築物以上"
        else:
            required = "防火構造等（技術的基準による）"
        items.append(
            CheckItem(
                "集団規定",
                "防火地域内の構造制限",
                "法61条",
                required,
                f"{zoning.fire_zone.value} / {building.structure.value} "
                f"{building.storeys}階・{total_area:.0f}m²",
                CHECK,
                "仕様（耐火・準耐火の別）を設計で確定する必要がある",
            )
        )

    # ---------- 単体規定 ----------
    for floor in building.floors:
        for name, area_m2, window_area, ok in daylight_check(floor):
            items.append(
                CheckItem(
                    "単体規定",
                    f"{floor.storey}階 {name} の採光",
                    "法28条1項・令19条",
                    f"床面積の1/7（{area_m2 * DAYLIGHT_RATIO:.2f}m²）以上",
                    f"開口部 {window_area:.2f}m²",
                    _verdict(ok),
                    "採光補正係数は隣地との距離により変動するため要確認",
                )
            )
            vent_ok = window_area >= area_m2 * VENTILATION_RATIO
            items.append(
                CheckItem(
                    "単体規定",
                    f"{floor.storey}階 {name} の換気",
                    "法28条2項",
                    f"床面積の1/20（{area_m2 * VENTILATION_RATIO:.2f}m²）以上"
                    "、不足時は換気設備",
                    f"開口部 {window_area:.2f}m²",
                    _verdict(vent_ok),
                )
            )

    ceiling = building.floors[0].ceiling_height_m
    items.append(
        CheckItem(
            "単体規定",
            "居室の天井高さ",
            "令21条",
            f"{MIN_CEILING_M:.1f}m 以上",
            f"{ceiling:.2f}m（階高 {building.floors[0].height_m:.2f}m）",
            _verdict(ceiling >= MIN_CEILING_M),
        )
    )

    stair = stair_dimensions(building)
    if stair:
        stair_w, riser, tread, steps = stair
        items.append(
            CheckItem(
                "単体規定",
                "階段の寸法",
                "令23条",
                f"幅{MIN_STAIR_WIDTH_M * 100:.0f}cm以上 / 蹴上{MAX_RISER_M * 100:.0f}cm以下 "
                f"/ 踏面{MIN_TREAD_M * 100:.0f}cm以上",
                f"幅{stair_w * 100:.0f}cm / 蹴上{riser * 100:.1f}cm / "
                f"踏面{tread * 100:.1f}cm（{steps}段）",
                _verdict(
                    stair_w >= MIN_STAIR_WIDTH_M
                    and riser <= MAX_RISER_M + 1e-6
                    and tread >= MIN_TREAD_M - 1e-6
                ),
                "直階段として算定。回り階段とする場合は踏面の測り方が異なる",
            )
        )

    items.append(
        CheckItem(
            "単体規定",
            "シックハウス対策（24時間換気）",
            "法28条の2・令20条の7〜9",
            "機械換気設備（0.5回/h）と内装制限",
            "換気設備の設置を前提",
            CHECK,
            "換気経路・建材の F☆☆☆☆ 確認が必要",
        )
    )

    # ---------- 手続き・別途検討 ----------
    items.append(
        CheckItem(
            "手続き",
            "構造安全性",
            "令46条ほか",
            "壁量計算または構造計算",
            "未検討",
            CHECK,
            "本ツールは構造計算を行わない",
        )
    )
    items.append(
        CheckItem(
            "手続き",
            "省エネ基準適合",
            "建築物省エネ法",
            "省エネ基準への適合（2025年4月〜全新築が対象）",
            "未検討",
            CHECK,
            "外皮性能・一次エネルギー消費量の計算が必要",
        )
    )
    return ComplianceReport(items)


def to_markdown(report: ComplianceReport) -> str:
    """チェック結果を Markdown の表にする。"""
    mark = {PASS: "✓", FAIL: "✗", CHECK: "△"}
    lines = [
        "# 法適合チェック（確認申請 事前チェック）",
        "",
        f"適合 {len(report.passed)} / 不適合 {len(report.failed)} / 要確認 {len(report.to_confirm)}",
        "",
    ]
    if report.failed:
        lines += ["## 不適合", ""]
        for item in report.failed:
            lines.append(f"- **{item.name}**（{item.law}）要求: {item.required} / 実績: {item.actual}")
        lines.append("")

    current = ""
    for item in report.items:
        if item.category != current:
            current = item.category
            lines += ["", f"## {current}", "", "| 判定 | 項目 | 根拠 | 要求 | 実績 | 備考 |",
                      "| :-: | --- | --- | --- | --- | --- |"]
        lines.append(
            f"| {mark[item.result]} | {item.name} | {item.law} | {item.required} | "
            f"{item.actual} | {item.note} |"
        )
    lines += [
        "",
        "---",
        "",
        "本チェックは自動判定であり、確認申請の審査を代替するものではありません。",
        "構造計算・省エネ計算・日影図・天空率は対象外です。",
        "",
    ]
    return "\n".join(lines)
