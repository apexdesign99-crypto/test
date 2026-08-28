"""建築費・総事業費の概算。

坪単価ベースの積み上げ方式で、**工事原価と粗利を分けて**計算する。

  工事原価 = 工事原価坪単価 × 延床坪数（付帯工事・現場経費を含む）
  請負金額 = 工事原価 ÷ (1 − 粗利率)
  建築費   = 請負金額 + 設計監理費 + 申請・調査費 (+ 消費税)
  総事業費 = 土地代 + 建築費 + 諸費用（仲介・登記・税・保険・ローン・予備費）

事業形態は 2 つを扱う。

  注文住宅  建築主が請負金額を支払う。総事業費＝土地＋建築費＋諸費用
  分譲住宅  事業者が土地を仕入れて建てて売る。`spec_development()` で
            販売価格・原価・販管費・事業利益を算出する

既定値は事業者の実績・設定に基づく（延床35坪・標準グレード）。

  工事原価  1,600万円 → 457,000 円/坪（付帯工事・現場経費を含む）
  粗利率    (2,000万 − 1,600万) ÷ 2,000万 = 20%
  請負      2,000万円 → 571,000 円/坪
  分譲      目標事業利益率 18% ／ 販管費 7% ／ 土地仕入諸費用 6%
  設計監理費 請負金額の 8%

案件ごとに変える場合は `Rates` と `Options`（`unit_cost_per_tsubo` /
`gross_margin` / `spec_target_margin` / `sale_price_jpy`）で差し替える。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import Building, CostBreakdown, CostItem, Site, Structure

TSUBO_M2 = 3.305785

#: 構造別の**工事原価** 坪単価 [円/坪]（標準グレード・付帯工事と現場経費を含む）
#: 木造は実績値（延床35坪・工事原価1,600万円 → 457,000円/坪）を既定とする。
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

    #: 付帯工事（外構・給排水引込・地盤改良）を別建てにする場合の率。
    #: 既定 0 は「工事原価の坪単価に含む」という意味。
    incidental: float = 0.0
    site_overhead: float = 0.0  # 現場経費（仮設・運搬・現場管理）を別建てにする場合の率
    gross_margin: float = 0.20  # 粗利率（粗利 ÷ 請負金額）＝実績 (2000-1600)/2000
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
    note = f"{structure.value}・{grade} {unit:,}円/坪 × {tsubo:.1f}坪"
    if not rates.incidental and not rates.site_overhead:
        note += "（付帯工事・現場経費を含む）"
    items = [CostItem("工事原価", main, note)]
    if rates.incidental:
        items.append(
            CostItem(
                "付帯工事原価",
                int(round(main * rates.incidental)),
                f"外構・給排水引込・地盤改良（本体の{rates.incidental:.0%}）",
            )
        )
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


#: 分譲事業の既定値（事業者の設定として確認済み）
SPEC_TARGET_MARGIN = 0.18  # 目標事業利益率（利益 ÷ 販売価格）
SPEC_SGA_RATE = 0.07  # 販売管理費（広告・販売手数料・金利など）÷ 販売価格
SPEC_ACQUISITION_RATE = 0.06  # 土地仕入諸費用（仲介・登記・取得税）÷ 土地価格


@dataclass
class SpecDevelopment:
    """分譲（建売）事業の収支。

    事業者が土地を仕入れ、建てて、土地建物一体で売る場合の採算を見る。
    """

    land_cost_jpy: int  # 土地仕入価格
    acquisition_cost_jpy: int  # 土地仕入諸費用
    construction_cost_jpy: int  # 工事原価
    design_cost_jpy: int  # 設計・申請・調査
    sga_jpy: int  # 販売管理費
    sale_price_jpy: int  # 販売価格（税込）
    target_margin: float

    @property
    def total_cost_jpy(self) -> int:
        return (
            self.land_cost_jpy
            + self.acquisition_cost_jpy
            + self.construction_cost_jpy
            + self.design_cost_jpy
            + self.sga_jpy
        )

    @property
    def profit_jpy(self) -> int:
        return self.sale_price_jpy - self.total_cost_jpy

    @property
    def profit_rate(self) -> float:
        return self.profit_jpy / self.sale_price_jpy if self.sale_price_jpy else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "land_cost_jpy": self.land_cost_jpy,
            "acquisition_cost_jpy": self.acquisition_cost_jpy,
            "construction_cost_jpy": self.construction_cost_jpy,
            "design_cost_jpy": self.design_cost_jpy,
            "sga_jpy": self.sga_jpy,
            "total_cost_jpy": self.total_cost_jpy,
            "sale_price_jpy": self.sale_price_jpy,
            "profit_jpy": self.profit_jpy,
            "profit_rate": round(self.profit_rate, 4),
            "target_margin": self.target_margin,
        }


def spec_development(
    breakdown: CostBreakdown,
    sale_price_jpy: Optional[int] = None,
    target_margin: float = SPEC_TARGET_MARGIN,
    sga_rate: float = SPEC_SGA_RATE,
    acquisition_rate: float = SPEC_ACQUISITION_RATE,
    tax_rate: float = 0.10,
) -> SpecDevelopment:
    """分譲事業の収支を算出する。

    販売価格を指定しない場合は、目標利益率から逆算する。
    建物には消費税がかかるため、工事原価・設計費に税を乗せて原価とする。
    """
    land = breakdown.land_price_jpy
    acquisition = int(round(land * acquisition_rate))
    construction = int(round(breakdown.cost_subtotal_jpy * (1 + tax_rate)))
    design = int(round(breakdown.soft_subtotal_jpy * (1 + tax_rate)))

    fixed_cost = land + acquisition + construction + design
    if sale_price_jpy is None:
        divisor = max(0.05, 1 - target_margin - sga_rate)
        sale_price_jpy = int(round(fixed_cost / divisor))
    sga = int(round(sale_price_jpy * sga_rate))

    return SpecDevelopment(
        land_cost_jpy=land,
        acquisition_cost_jpy=acquisition,
        construction_cost_jpy=construction,
        design_cost_jpy=design,
        sga_jpy=sga,
        sale_price_jpy=sale_price_jpy,
        target_margin=target_margin,
    )


def unit_cost_per_tsubo(breakdown: CostBreakdown, total_floor_area_m2: float) -> int:
    """建築費（税込）ベースの坪単価 [円/坪]。"""
    tsubo = total_floor_area_m2 / TSUBO_M2
    return int(round(breakdown.construction_total_jpy / tsubo)) if tsubo > 0 else 0


def unit_cost_price_per_tsubo(breakdown: CostBreakdown, total_floor_area_m2: float) -> int:
    """工事原価ベースの坪単価 [円/坪]。"""
    tsubo = total_floor_area_m2 / TSUBO_M2
    return int(round(breakdown.cost_subtotal_jpy / tsubo)) if tsubo > 0 else 0
