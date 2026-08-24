"""建築費・総事業費の概算。

坪単価ベースの積み上げ方式。単価テーブル（`UNIT_PRICE_PER_TSUBO`）と
料率（`Rates`）は目安値なので、事業者ごとの実績値に差し替えて使う。

  建築費 = 本体工事費 + 付帯工事費 + 設計監理費 + 申請・調査費 (+ 消費税)
  総事業費 = 土地代 + 建築費 + 諸費用（仲介・登記・税・保険・ローン・予備費）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import Building, CostBreakdown, CostItem, Site, Structure

TSUBO_M2 = 3.305785

#: 構造別の本体工事費 坪単価 [円/坪]（標準グレード）
UNIT_PRICE_PER_TSUBO: Dict[Structure, int] = {
    Structure.WOOD: 900_000,
    Structure.STEEL: 1_150_000,
    Structure.RC: 1_350_000,
}

#: グレード係数
GRADE_FACTOR: Dict[str, float] = {
    "ローコスト": 0.80,
    "標準": 1.00,
    "ハイグレード": 1.30,
}


@dataclass
class Rates:
    """料率・固定費の設定。"""

    incidental: float = 0.15  # 付帯工事（外構・給排水引込・地盤改良）
    design: float = 0.08  # 設計監理費
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


def construction_items(
    total_floor_area_m2: float,
    structure: Structure,
    grade: str = "標準",
    rates: Optional[Rates] = None,
) -> List[CostItem]:
    """建築費の内訳を返す（税抜）。"""
    rates = rates or Rates()
    tsubo = total_floor_area_m2 / TSUBO_M2
    unit = int(round(UNIT_PRICE_PER_TSUBO[structure] * GRADE_FACTOR.get(grade, 1.0)))
    main = int(round(tsubo * unit))
    incidental = int(round(main * rates.incidental))
    design = int(round(main * rates.design))
    return [
        CostItem(
            "本体工事費",
            main,
            f"{structure.value}・{grade} {unit:,}円/坪 × {tsubo:.1f}坪",
        ),
        CostItem("付帯工事費", incidental, f"外構・給排水引込・地盤改良（本体の{rates.incidental:.0%}）"),
        CostItem("設計・監理費", design, f"本体の{rates.design:.0%}"),
        CostItem("確認申請・検査費", rates.application_fee_jpy, "建築確認・中間/完了検査・省エネ適合判定"),
        CostItem("地盤調査・測量費", rates.survey_fee_jpy, "スウェーデン式サウンディング・現況測量"),
    ]


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
) -> CostBreakdown:
    """建築費と総事業費をまとめて算出する。"""
    rates = rates or Rates()
    land = land_price_jpy if land_price_jpy is not None else (site.land_price_jpy or 0)
    c_items = construction_items(building.total_floor_area_m2, building.structure, grade, rates)
    construction_total = int(
        round(sum(i.amount_jpy for i in c_items) * (1 + rates.consumption_tax))
    )
    o_items = other_items(land, construction_total, rates)
    return CostBreakdown(
        construction_items=c_items,
        other_items=o_items,
        land_price_jpy=land,
        tax_rate=rates.consumption_tax,
    )


def unit_cost_per_tsubo(breakdown: CostBreakdown, total_floor_area_m2: float) -> int:
    """建築費（税込）ベースの坪単価 [円/坪]。"""
    tsubo = total_floor_area_m2 / TSUBO_M2
    return int(round(breakdown.construction_total_jpy / tsubo)) if tsubo > 0 else 0
