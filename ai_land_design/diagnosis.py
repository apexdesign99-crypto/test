"""AI 土地診断。

不動産 API と GIS から集めた条件を 5 つの評価軸に分解し、
重み付き加算でスコア（0〜100）とランク（S/A/B/C/D）を出す。

「AI」といっても中身はブラックボックスではなく、評価軸ごとの
決定的なスコアリング関数の集合として実装している。各項目のスコアと
コメントを返すので、なぜその点数になったかを常に説明できる。
重み（`WEIGHTS`）を差し替えれば事業主体ごとの評価方針に合わせられる。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import Diagnosis, Finding, ScoreItem, Site, UseDistrict

#: 評価軸の重み（合計 1.0）
WEIGHTS: Dict[str, float] = {
    "法規ポテンシャル": 0.25,
    "敷地形状": 0.20,
    "接道条件": 0.20,
    "立地・環境": 0.20,
    "価格妥当性": 0.15,
}

RANK_THRESHOLDS = [(85, "S"), (70, "A"), (55, "B"), (40, "C")]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def score_regulation(site: Site) -> ScoreItem:
    """法規ポテンシャル：建蔽率・容積率の大きさで土地の使い代を評価。"""
    z = site.zoning
    potential = z.building_coverage_ratio * 100 * 0.4 + z.floor_area_ratio * 100 * 0.2
    score = _clamp(potential)
    comment = f"建蔽率{z.building_coverage_ratio * 100:.0f}% / 容積率{z.floor_area_ratio * 100:.0f}%"
    if z.use_district.is_low_rise:
        comment += "（低層住居専用：閑静だがボリュームは小さい）"
    return ScoreItem("法規ポテンシャル", score, WEIGHTS["法規ポテンシャル"], comment)


def score_shape(site: Site) -> ScoreItem:
    """敷地形状：整形度と面積規模。"""
    regularity = site.regularity
    area_score = _clamp((site.area_m2 - 60) / 100 * 60 + 40)
    score = _clamp(regularity * 100 * 0.7 + area_score * 0.3)
    comment = f"整形度{regularity:.2f} / 面積{site.area_m2:.1f}m2（{site.area_tsubo:.1f}坪）"
    if regularity < 0.75:
        comment += "：不整形。プラン効率の低下に注意"
    return ScoreItem("敷地形状", score, WEIGHTS["敷地形状"], comment)


def score_access(site: Site) -> ScoreItem:
    """接道条件：幅員・接道長・方位・角地。"""
    road = site.widest_road
    if road is None:
        return ScoreItem("接道条件", 0.0, WEIGHTS["接道条件"], "建築基準法上の道路に接していない")

    width_score = _clamp((road.width_m - 2.0) / 4.0 * 100)  # 6m で満点
    frontage_score = _clamp((road.frontage_m - 2.0) / 8.0 * 100)  # 10m で満点
    direction_bonus = {"南": 100.0, "東": 80.0, "西": 70.0, "北": 55.0}[road.direction.value]
    score = width_score * 0.4 + frontage_score * 0.25 + direction_bonus * 0.35
    if site.zoning.is_corner_lot:
        score = _clamp(score + 8)
    comment = (
        f"{road.direction.value}側 幅員{road.width_m:.1f}m / 接道{road.frontage_m:.1f}m"
        + ("・角地" if site.zoning.is_corner_lot else "")
    )
    return ScoreItem("接道条件", _clamp(score), WEIGHTS["接道条件"], comment)


def score_location(site: Site) -> ScoreItem:
    """立地・環境：駅距離とハザード。"""
    if site.station_distance_m is None:
        station_score = 60.0
        station_comment = "駅距離データなし"
    else:
        station_score = _clamp(100 - (site.station_distance_m - 400) / 12.0)
        minutes = round(site.station_distance_m / 80)
        station_comment = f"駅徒歩約{minutes}分（{site.station_distance_m}m）"

    h = site.hazard
    penalty = 0.0
    hazard_notes: List[str] = []
    if h.flood_depth_m > 0:
        penalty += min(35.0, h.flood_depth_m * 20)
        hazard_notes.append(f"浸水想定{h.flood_depth_m:.1f}m")
    if h.landslide_risk:
        penalty += 25.0
        hazard_notes.append("土砂災害警戒区域")
    if h.liquefaction_risk:
        penalty += 15.0
        hazard_notes.append("液状化リスク")
    penalty += max(0, h.quake_intensity_rank - 3) * 5.0

    score = _clamp(station_score - penalty)
    comment = station_comment + ("／" + "・".join(hazard_notes) if hazard_notes else "／ハザード指定なし")
    return ScoreItem("立地・環境", score, WEIGHTS["立地・環境"], comment)


def score_price(site: Site, market_unit_price_per_tsubo: Optional[int] = None) -> ScoreItem:
    """価格妥当性：周辺相場（坪単価中央値）との比較。"""
    unit = site.unit_price_per_tsubo
    if unit is None:
        return ScoreItem("価格妥当性", 50.0, WEIGHTS["価格妥当性"], "価格情報なし（中立評価）")
    if not market_unit_price_per_tsubo:
        return ScoreItem(
            "価格妥当性",
            55.0,
            WEIGHTS["価格妥当性"],
            f"坪単価{unit:,}円（比較する相場データなし）",
        )
    ratio = unit / market_unit_price_per_tsubo
    score = _clamp(100 - (ratio - 0.85) * 200)  # 相場の 85% 以下で満点
    comment = (
        f"坪単価{unit:,}円 / 相場{market_unit_price_per_tsubo:,}円（{ratio * 100:.0f}%）"
    )
    return ScoreItem("価格妥当性", score, WEIGHTS["価格妥当性"], comment)


def _rank(total: float) -> str:
    for threshold, rank in RANK_THRESHOLDS:
        if total >= threshold:
            return rank
    return "D"


def diagnose(site: Site, market_unit_price_per_tsubo: Optional[int] = None) -> Diagnosis:
    """敷地を診断し、スコア・ランク・指摘事項を返す。"""
    items = [
        score_regulation(site),
        score_shape(site),
        score_access(site),
        score_location(site),
        score_price(site, market_unit_price_per_tsubo),
    ]
    total = sum(i.score * i.weight for i in items)

    findings: List[Finding] = []
    if site.widest_road is None:
        findings.append(Finding("block", "DIAG_NO_ROAD", "接道なし。再建築不可物件の可能性が高い。"))
    elif site.widest_road.width_m < 4.0:
        findings.append(
            Finding("warn", "DIAG_NARROW_ROAD", "前面道路が4m未満。セットバックと工事車両搬入に注意。")
        )
    if site.regularity < 0.7:
        findings.append(Finding("warn", "DIAG_SHAPE", "不整形地。有効な建築面積が想定より小さくなる。"))
    if site.hazard.flood_depth_m >= 0.5:
        findings.append(
            Finding(
                "warn",
                "DIAG_FLOOD",
                f"浸水想定{site.hazard.flood_depth_m:.1f}m。基礎高さ・電気設備配置の検討が必要。",
            )
        )
    if site.hazard.landslide_risk:
        findings.append(
            Finding("warn", "DIAG_LANDSLIDE", "土砂災害警戒区域。擁壁・構造安全性の検討が必要。")
        )
    if site.zoning.use_district is UseDistrict.EXCLUSIVE_INDUSTRIAL:
        findings.append(Finding("block", "DIAG_USE", "工業専用地域のため住宅用途では検討不可。"))
    if site.area_m2 < 60:
        findings.append(Finding("warn", "DIAG_SMALL", "敷地面積が狭小。プランの自由度が大きく制約される。"))

    return Diagnosis(total_score=total, rank=_rank(total), items=items, findings=findings)
