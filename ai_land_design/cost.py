"""建築費・総事業費の概算。

坪単価ベースの積み上げ方式で、**工事原価と粗利を分けて**計算する。

  工事原価 = 本体工事原価 + 付帯工事原価 + 現場経費
  請負金額 = 工事原価 ÷ (1 − 粗利率)
  建築費   = 請負金額 + 設計監理費 + 申請・調査費 (+ 消費税)
  総事業費 = 土地代 + 建築費 + 諸費用（仲介・登記・税・保険・ローン・予備費）

原価単価（`UNIT_COST_PER_TSUBO`）と料率（`Rates`）は事業者ごとの実績値に
差し替えて使う。木造の既定値は実績（35坪・1,600万円）に基づく 457,000 円/坪。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import Building, CostBreakdown, CostItem, Site, Structure

TSUBO_M2 = 3.305785

#: 構造別の本体工事**原価** 坪単価 [円/坪]（標準グレード）
#: 木造は実績値（延床35坪・本体工事原価1,600万円 → 457,000円/坪）を既定とする。
UNIT_COST_PER_TSUBO: Dict[Structure, int] = {
    Structure.WOOD: 457_000,
    Structure.STEEL: 580_000,
    Structure.RC: 690_000,
}

#: 後方互換のための別名（旧 API は請負単価を指していた点に注意）
UNIT_PRICE_PER_TSUBO = UNIT_COST_PER_TSUBO

#: グレード係数
GRADE_FACTOR: Dict[str, float] = {
    "ローコスト": 0.80,
    "標準": 1.00,
    "ハイグレード": 1.30,
}


@dataclass
class Rates:
    """料率・固定費の設定。"""

    incidental: float = 0.15  # 付帯工事（外構・給排水引込・地盤改良）の原価
    site_overhead: float = 0.05  # 現場経費（仮設・運搬・現場管理）
    gross_margin: float = 0.25  # 粗利率（粗利 ÷ 請負金額）
    design: float = 0.08  # 設計監理費（請負金額に対する率）
    application_fee_jpy: int = 350_000  # 確認申請・中間/完了検査・省エネ適合
    survey_fee_jpy: int = 250_000  # 地盤調査・測量
    consumption_tax: float = 0.10
    brokerage_rate: float = 0.03  # 仲介手数料（速算式）
    brokerage_fixed_jpy: int = 60_000
    registration_jpy: int = 600_000  # 登記費用（土地・建物）
    acquisition_tax_rate: float = 0.005  # 不動産取得税（軽減後の概算）
    insurance_jpy: int = 300_000  # 火災・地震保険
    loan_fee_rate: float = 0.022  # ローン事務手数料等
    loan_ratio: float = 0.80  # 借入比率
    contingency: float = 0.03  # 予備費


def cost_items(
    total_floor_area_m2: float,
    structure: Structure,
    grade: str = "標準",
    rates: Optional[Rates] = None,
    unit_cost_per_tsubo: Optional[int] = None,
) -> List[CostItem]:
    """工事原価の内訳を返す（税抜）。

    `unit_cost_per_tsubo` を渡すと、その原価坪単価で計算する（実績値の反映用）。
    """
    rates = rates or Rates()
    tsubo = total_floor_area_m2 / TSUBO_M2
    base = unit_cost_per_tsubo or UNIT_COST_PER_TSUBO[structure]
    unit = int(round(base * GRADE_FACTOR.get(grade, 1.0)))
    main = int(round(tsubo * unit))
    items = [
        CostItem(
            "本体工事原価",
            main,
            f"{structure.value}・{grade} {unit:,}円/坪 × {tsubo:.1f}坪",
        ),
        CostItem(
            "付帯工事原価",
            int(round(main * rates.incidental)),
            f"外構・給排水引込・地盤改良（本体の{rates.incidental:.0%}）",
        ),
    ]
    if rates.site_overhead:
        items.append(
            CostItem(
                "現場経費",
                int(round(main * rates.site_overhead)),
                f"仮設・運搬・現場管理（本体の{rates.site_overhead:.0%}）",
            )
        )
    return items


def soft_items(contract_jpy: int, rates: Optional[Rates] = None) -> List[CostItem]:
    """設計監理・申請・調査の費用（税抜）。"""
    rates = rates or Rates()
    return [
        CostItem("設計・監理費", int(round(contract_jpy * rates.design)),
                 f"請負金額の{rates.design:.0%}"),
        CostItem("確認申請・検査費", rates.application_fee_jpy,
                 "建築確認・中間/完了検査・省エネ適合判定"),
        CostItem("地盤調査・測量費", rates.survey_fee_jpy,
                 "スウェーデン式サウンディング・現況測量"),
    ]


def margin_for(cost_subtotal_jpy: int, rates: Optional[Rates] = None) -> int:
    """工事原価から粗利額を求める（粗利率は請負金額に対する割合）。"""
    rates = rates or Rates()
    margin_rate = min(max(rates.gross_margin, 0.0), 0.9)
    if margin_rate <= 0:
        return 0
    contract = cost_subtotal_jpy / (1 - margin_rate)
    return int(round(contract - cost_subtotal_jpy))


def construction_items(
    total_floor_area_m2: float,
    structure: Structure,
    grade: str = "標準",
    rates: Optional[Rates] = None,
) -> List[CostItem]:
    """工事原価の内訳（後方互換のための別名）。"""
    return cost_items(total_floor_area_m2, structure, grade, rates)


def other_items(
    land_price_jpy: int,
    construction_total_jpy: int,
    rates: Optional[Rates] = None,
) -> List[CostItem]:
    """土地取得・諸費用の内訳を返す。"""
    rates = rates or Rates()
    items: List[CostItem] = []
    if land_price_jpy > 0:
        brokerage = int(
            round((land_price_jpy * rates.brokerage_rate + rates.brokerage_fixed_jpy) * 1.1)
        )
        items.append(CostItem("仲介手数料", brokerage, "速算式（3%+6万）×消費税"))
        items.append(
            CostItem(
                "不動産取得税・印紙税等",
                int(round(land_price_jpy * rates.acquisition_tax_rate)),
                "軽減措置適用後の概算",
            )
        )
    items.append(CostItem("登記費用", rates.registration_jpy, "所有権移転・保存登記、司法書士報酬"))
    items.append(CostItem("火災・地震保険", rates.insurance_jpy, "10年一括の目安"))
    loan_amount = (land_price_jpy + construction_total_jpy) * rates.loan_ratio
    items.append(
        CostItem(
            "ローン諸費用",
            int(round(loan_amount * rates.loan_fee_rate)),
            f"借入比率{rates.loan_ratio:.0%}・事務手数料{rates.loan_fee_rate:.1%}",
        )
    )
    subtotal = land_price_jpy + construction_total_jpy + sum(i.amount_jpy for i in items)
    items.append(CostItem("予備費", int(round(subtotal * rates.contingency)), f"総額の{rates.contingency:.0%}"))
    return items


def estimate(
    site: Site,
    building: Building,
    grade: str = "標準",
    rates: Optional[Rates] = None,
    land_price_jpy: Optional[int] = None,
    unit_cost_per_tsubo: Optional[int] = None,
) -> CostBreakdown:
    """工事原価・請負金額・総事業費をまとめて算出する。"""
    rates = rates or Rates()
    land = land_price_jpy if land_price_jpy is not None else (site.land_price_jpy or 0)

    cost = cost_items(
        building.total_floor_area_m2, building.structure, grade, rates, unit_cost_per_tsubo
    )
    cost_subtotal = sum(i.amount_jpy for i in cost)
    margin = margin_for(cost_subtotal, rates)
    soft = soft_items(cost_subtotal + margin, rates)

    construction_total = int(
        round((cost_subtotal + margin + sum(i.amount_jpy for i in soft)) * (1 + rates.consumption_tax))
    )
    return CostBreakdown(
        construction_items=cost,
        margin_jpy=margin,
        soft_items=soft,
        other_items=other_items(land, construction_total, rates),
        land_price_jpy=land,
        tax_rate=rates.consumption_tax,
    )


def unit_cost_per_tsubo(breakdown: CostBreakdown, total_floor_area_m2: float) -> int:
    """建築費（税込）ベースの坪単価 [円/坪]。"""
    tsubo = total_floor_area_m2 / TSUBO_M2
    return int(round(breakdown.construction_total_jpy / tsubo)) if tsubo > 0 else 0


def unit_cost_price_per_tsubo(breakdown: CostBreakdown, total_floor_area_m2: float) -> int:
    """工事原価ベースの坪単価 [円/坪]。"""
    tsubo = total_floor_area_m2 / TSUBO_M2
    return int(round(breakdown.cost_subtotal_jpy / tsubo)) if tsubo > 0 else 0
