"""建築可能判定。

建築基準法の主要な形態規制を、massing（ボリューム）検討レベルで機械判定する。

判定項目
  1. 接道義務（法43条）と 42条2項道路のセットバック
  2. 建蔽率（法53条）と角地・防火地域の緩和
  3. 容積率（法52条）と前面道路幅員による制限
  4. 高さ制限：絶対高さ（法55条）／道路斜線・隣地斜線・北側斜線（法56条）
  5. 上記から建築可能ボリューム（建築面積・延床面積・高さ・階数）

注意: 天空率・日影規制の詳細計算、地区計画・条例の個別規定は対象外。
      出力は概算であり、確認申請には建築士による精査が必要。
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from .geometry import bbox
from .models import (
    Direction,
    Envelope,
    FireZone,
    Finding,
    HeightLimit,
    Road,
    Site,
    UseDistrict,
)

#: 階高の既定値 [m]
DEFAULT_FLOOR_HEIGHT_M = 2.9
#: 1 階床レベル + 屋根までの余裕 [m]
ROOF_ALLOWANCE_M = 1.6
#: 境界からの想定外壁後退 [m]（外壁後退指定がない場合の民法 234 条相当）
DEFAULT_WALL_SETBACK_M = 0.5


def road_slope(use: UseDistrict) -> float:
    """道路斜線の勾配。"""
    return 1.25 if use.is_residential_group else 1.5


def applicable_distance_m(use: UseDistrict, far: float) -> float:
    """道路斜線の適用距離（法別表第3、簡略版） [m]。"""
    far_pct = far * 100
    if use.is_residential_group:
        table = [(200, 20), (300, 25), (400, 30)]
        fallback = 35
    elif use in (UseDistrict.NEIGHBORHOOD_COMMERCIAL, UseDistrict.COMMERCIAL):
        table = [(400, 20), (600, 25), (800, 30), (1000, 35), (1100, 40), (1200, 45)]
        fallback = 50
    else:
        table = [(200, 20), (300, 25), (400, 30)]
        fallback = 35
    for threshold, distance in table:
        if far_pct <= threshold:
            return float(distance)
    return float(fallback)


def setback_loss_m2(site: Site) -> Tuple[float, List[Finding]]:
    """42条2項道路のセットバックで失われる敷地面積 [m2]。

    道路中心線から 2.0m まで後退する（中心後退）ものとして算定する。
    """
    loss = 0.0
    findings: List[Finding] = []
    for road in site.roads:
        if not road.is_legal_road:
            continue
        if road.is_setback_road or road.width_m < 4.0:
            depth = max(0.0, (4.0 - road.width_m) / 2.0)
            if depth > 0:
                area = depth * road.frontage_m
                loss += area
                findings.append(
                    Finding(
                        "warn",
                        "SETBACK_42_2",
                        f"幅員{road.width_m:.1f}mの{road.direction.value}側道路は42条2項道路の可能性。"
                        f"中心後退{depth:.2f}mで約{area:.1f}m2が敷地面積・建築面積から除外。",
                    )
                )
    return loss, findings


def check_road_access(site: Site) -> Tuple[bool, List[Finding]]:
    """接道義務（幅員4m以上の道路に2m以上接する）の判定。"""
    findings: List[Finding] = []
    legal = [r for r in site.roads if r.is_legal_road]
    if not legal:
        findings.append(
            Finding("block", "NO_ROAD", "建築基準法上の道路に接していないため再建築不可。")
        )
        return False, findings

    ok = False
    for road in legal:
        effective_width = 4.0 if (road.is_setback_road or road.width_m < 4.0) else road.width_m
        if road.frontage_m >= 2.0 and effective_width >= 4.0:
            ok = True
    if not ok:
        widest = max(legal, key=lambda r: r.frontage_m)
        findings.append(
            Finding(
                "block",
                "ROAD_ACCESS",
                f"接道長が不足（最大{widest.frontage_m:.1f}m）。法43条の2m以上を満たさない。",
            )
        )
    return ok, findings


def applied_coverage_ratio(site: Site) -> Tuple[float, List[Finding]]:
    """緩和後の建蔽率（法53条3項・6項）。"""
    zoning = site.zoning
    bcr = zoning.building_coverage_ratio
    findings: List[Finding] = []
    fire_relaxation = zoning.fire_zone is FireZone.FIRE

    if fire_relaxation and abs(bcr - 0.8) < 1e-9:
        findings.append(
            Finding("info", "BCR_100", "防火地域内の耐火建築物のため建蔽率の制限なし（100%）。")
        )
        return 1.0, findings

    if zoning.is_corner_lot:
        bcr += 0.10
        findings.append(Finding("info", "BCR_CORNER", "角地緩和により建蔽率 +10%。"))
    if fire_relaxation:
        bcr += 0.10
        findings.append(
            Finding("info", "BCR_FIRE", "防火地域内の耐火建築物として建蔽率 +10%（要仕様確認）。")
        )
    return min(bcr, 1.0), findings


def applied_far(site: Site) -> Tuple[float, List[Finding]]:
    """前面道路幅員による容積率制限（法52条2項）を加味した容積率。"""
    zoning = site.zoning
    findings: List[Finding] = []
    road = site.widest_road
    if road is None:
        return 0.0, [Finding("block", "FAR_NO_ROAD", "前面道路がないため容積率を算定できない。")]

    width = max(road.width_m, 4.0) if (road.is_setback_road or road.width_m < 4.0) else road.width_m
    if width >= 12.0:
        return zoning.floor_area_ratio, findings

    coefficient = 0.4 if zoning.use_district.is_residential_group else 0.6
    road_far = width * coefficient
    if road_far < zoning.floor_area_ratio:
        findings.append(
            Finding(
                "warn",
                "FAR_ROAD_LIMIT",
                f"前面道路幅員{width:.1f}m×{coefficient}により容積率が"
                f"{zoning.floor_area_ratio * 100:.0f}% → {road_far * 100:.0f}% に制限。",
            )
        )
        return road_far, findings
    return zoning.floor_area_ratio, findings


def height_limits(
    site: Site,
    wall_setback_m: Optional[float] = None,
    building_depth_m: float = 8.0,
) -> List[HeightLimit]:
    """各高さ制限の上限値を算出する。

    `wall_setback_m` は境界から外壁までの想定後退距離。斜線制限の後退緩和に用いる。
    北側斜線は棟位置（北側外壁 + 建物奥行の 1/2）で評価する massing 近似。
    """
    zoning = site.zoning
    use = zoning.use_district
    setback = wall_setback_m if wall_setback_m is not None else max(
        zoning.wall_setback_m, DEFAULT_WALL_SETBACK_M
    )
    limits: List[HeightLimit] = []

    if zoning.height_limit_m:
        limits.append(
            HeightLimit("絶対高さ制限", float(zoning.height_limit_m), "法55条（低層住居専用地域等）")
        )
    elif use.is_low_rise:
        limits.append(HeightLimit("絶対高さ制限", 10.0, "低層住居専用地域の既定値 10m を適用"))

    road = site.widest_road
    if road is not None:
        width = max(road.width_m, 4.0)
        slope = road_slope(use)
        horizontal = width + 2 * setback
        limit_distance = applicable_distance_m(use, zoning.floor_area_ratio)
        if horizontal >= limit_distance:
            limits.append(
                HeightLimit(
                    "道路斜線制限",
                    999.0,
                    f"水平距離{horizontal:.1f}m が適用距離{limit_distance:.0f}m 以上のため適用外",
                )
            )
        else:
            limits.append(
                HeightLimit(
                    "道路斜線制限",
                    slope * horizontal,
                    f"勾配{slope} ×(道路幅員{width:.1f}m + 後退{setback:.1f}m×2)",
                )
            )

    if not use.is_low_rise:
        if use.is_residential_group:
            base, adj_slope = 20.0, 1.25
        else:
            base, adj_slope = 31.0, 2.5
        limits.append(
            HeightLimit(
                "隣地斜線制限",
                base + adj_slope * (2 * setback),
                f"立上り{base:.0f}m + 勾配{adj_slope} ×(後退{setback:.1f}m×2)",
            )
        )

    if use.is_low_rise or use.is_mid_high:
        base = 5.0 if use.is_low_rise else 10.0
        north_distance = setback + building_depth_m / 2.0
        limits.append(
            HeightLimit(
                "北側斜線制限",
                base + 1.25 * north_distance,
                f"立上り{base:.0f}m + 1.25 ×真北距離{north_distance:.1f}m（棟位置で評価）",
            )
        )

    if not limits:
        limits.append(HeightLimit("高さ制限なし", 999.0, "形態規制上の高さ制限は指定なし"))
    return limits


def estimated_setback_m(site: Site, max_building_area_m2: float) -> float:
    """建蔽率いっぱいに建てた場合に確保できる、道路境界からの外壁後退距離 [m]。

    斜線制限は建物の位置で決まるため、固定値ではなく敷地と建築面積から見積もる。
    建築面積を正方形に近い形で敷地中央に置いたと仮定し、道路側に残る距離を返す。
    実際の設計で建物をさらに後退させれば、この値より緩和は大きくなる。
    """
    min_x, min_y, max_x, max_y = bbox(site.polygon)
    width, depth = max_x - min_x, max_y - min_y
    if max_building_area_m2 <= 0 or width <= 0 or depth <= 0:
        return DEFAULT_WALL_SETBACK_M

    side = math.sqrt(max_building_area_m2)
    road = site.widest_road
    if road is not None and road.direction in (Direction.E, Direction.W):
        available, building_side = width, min(width, side)
    else:
        available, building_side = depth, min(depth, side)

    setback = max(0.0, (available - building_side) / 2)
    return max(setback, site.zoning.wall_setback_m, DEFAULT_WALL_SETBACK_M)


def evaluate(
    site: Site,
    floor_height_m: float = DEFAULT_FLOOR_HEIGHT_M,
    max_storeys_cap: int = 3,
) -> Envelope:
    """敷地から建築可能ボリュームを算出する。"""
    findings: List[Finding] = []

    access_ok, access_findings = check_road_access(site)
    findings.extend(access_findings)

    loss, loss_findings = setback_loss_m2(site)
    findings.extend(loss_findings)
    effective_area = max(0.0, site.area_m2 - loss)

    bcr, bcr_findings = applied_coverage_ratio(site)
    findings.extend(bcr_findings)

    far, far_findings = applied_far(site)
    findings.extend(far_findings)

    if not site.zoning.use_district.allows_dwelling:
        findings.append(
            Finding(
                "block",
                "USE_NOT_ALLOWED",
                f"{site.zoning.use_district.value}では住宅を建築できない（法48条）。",
            )
        )

    max_building_area = effective_area * bcr
    max_floor_area = effective_area * far

    # 想定建物奥行（正方形近似）と、敷地に対する建物位置から斜線制限を評価する。
    depth = math.sqrt(max_building_area) if max_building_area > 0 else 8.0
    setback = estimated_setback_m(site, max_building_area)
    limits = height_limits(site, wall_setback_m=setback, building_depth_m=depth)
    max_height = min(l.limit_m for l in limits)

    usable_height = max(0.0, max_height - ROOF_ALLOWANCE_M)
    storeys_by_height = int(usable_height // floor_height_m)
    storeys_by_area = (
        int(math.ceil(max_floor_area / max_building_area)) if max_building_area > 0 else 0
    )
    storeys = max(0, min(storeys_by_height, storeys_by_area, max_storeys_cap))

    if storeys_by_height < storeys_by_area and storeys_by_height > 0:
        findings.append(
            Finding(
                "warn",
                "HEIGHT_BINDS",
                f"高さ制限{max_height:.2f}mが効き、容積率を消化しきれない"
                f"（高さから{storeys_by_height}階 / 容積から{storeys_by_area}階）。",
            )
        )

    if storeys == 0:
        findings.append(
            Finding("block", "NO_VOLUME", "高さ・面積の制限により建築可能ボリュームが確保できない。")
        )

    if site.zoning.shadow_regulation:
        findings.append(
            Finding("warn", "SHADOW", "日影規制の指定あり。実施設計時に日影図による検証が必要。")
        )
    if site.zoning.scenic_district:
        findings.append(
            Finding("warn", "SCENIC", "風致地区等の指定あり。意匠・外構に追加規制の可能性。")
        )
    if site.zoning.wall_setback_m > 0:
        findings.append(
            Finding(
                "info",
                "WALL_SETBACK",
                f"外壁後退{site.zoning.wall_setback_m:.1f}mの指定あり（法54条）。",
            )
        )

    buildable = (
        access_ok
        and storeys > 0
        and site.zoning.use_district.allows_dwelling
        and max_building_area > 0
    )
    # 実際に配置できる延床面積は 建築面積 × 階数 が上限。
    realizable_floor_area = min(max_floor_area, max_building_area * storeys)

    return Envelope(
        buildable=buildable,
        effective_site_area_m2=effective_area,
        setback_loss_m2=loss,
        applied_coverage_ratio=bcr,
        applied_far=far,
        max_building_area_m2=max_building_area,
        max_floor_area_m2=realizable_floor_area,
        max_height_m=max_height,
        height_limits=limits,
        max_storeys=storeys,
        findings=findings,
    )
