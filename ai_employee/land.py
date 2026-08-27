"""土地診断——敷地条件から建てられるボリュームの目安を出す。

**この診断は法適合の判断ではない。**

やっていること:
  人が都市計画情報などで調べた規制値を入力として受け取り、
  建築面積・延床面積の上限を機械的に計算する。

やっていないこと:
  規制値そのものを推測すること。斜線制限・日影規制・地区計画・
  各種条例の判定。これらは計算せず、「確認すべき項目」として列挙する。

計算に使う係数や閾値は事務所プロフィールの設定値であり、
このモジュールが法令から導いたものではない。計画地に適用される規制は、
必ず所管行政庁と都市計画情報で確認すること。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 用途地域。「指定なし」は市街化調整区域・非線引き区域などを想定。
ZONING_TYPES: tuple[str, ...] = (
    "第一種低層住居専用地域",
    "第二種低層住居専用地域",
    "第一種中高層住居専用地域",
    "第二種中高層住居専用地域",
    "第一種住居地域",
    "第二種住居地域",
    "準住居地域",
    "田園住居地域",
    "近隣商業地域",
    "商業地域",
    "準工業地域",
    "工業地域",
    "工業専用地域",
    "指定なし",
)

# 前面道路幅員による容積率制限で「住居系」として扱う用途地域。
# この区分も事務所の設定で上書きできる(下の LandSettings 参照)。
DEFAULT_RESIDENTIAL_ZONES: tuple[str, ...] = ZONING_TYPES[:8]

# 建蔽率の緩和。適用可否は行政判断なので、既定では適用しない。
RELAXATIONS: tuple[tuple[str, str], ...] = (
    ("corner_lot", "角地等による緩和"),
    ("fireproof", "防火地域内の耐火建築物等による緩和"),
)

# 診断では判定できず、必ず人が確認する項目。
# 「計算していないこと」を毎回明示するためのリスト。
REQUIRED_CONFIRMATIONS: tuple[tuple[str, str], ...] = (
    ("道路種別", "前面道路が建築基準法上の道路か、何項道路か。2項道路ならセットバックの要否と後退距離。"),
    ("複数道路", "前面道路が複数ある場合の容積率算定の取扱い。"),
    ("斜線制限", "道路斜線・隣地斜線・北側斜線。この診断では計算していない。"),
    ("高度地区", "高度地区の指定と、絶対高さ・斜線の制限。"),
    ("日影規制", "日影規制の対象か、対象なら測定面と規制時間。"),
    ("地区計画", "地区計画・建築協定・景観計画による独自の制限。"),
    ("防火指定", "防火地域・準防火地域の指定と、要求される構造。"),
    ("用途制限", "計画している用途がその用途地域で建てられるか。"),
    ("敷地の分断", "用途地域や容積率が敷地内で異なる場合の按分。"),
    ("がけ・擁壁", "がけ条例の適用、既存擁壁の安全性と造り替えの要否。"),
    ("ハザード", "洪水・土砂災害警戒区域等の指定と、必要な対策。"),
    ("埋蔵文化財", "埋蔵文化財包蔵地の該当と、届出・試掘の要否。"),
    ("インフラ", "上下水道・ガスの引込状況と、負担金の有無。"),
    ("境界・越境", "境界確定の状況、越境物の有無。"),
)


class LandError(RuntimeError):
    """土地診断の入力が不正、または診断に必要な設定が足りない。"""


@dataclass
class LandSettings:
    """診断に使う係数と閾値。すべて事務所が確認して設定する値。

    既定値は一般に用いられる値だが、このモジュールが法令から導いたものではない。
    計画地に適用される値は所管行政庁で確認すること。
    """

    # 前面道路幅員による容積率制限の係数(幅員 m × 係数 ÷ 10 = 上限容積率 %)
    road_coefficient_residential: int = 4
    road_coefficient_other: int = 6
    # 接道義務の判定に使う閾値
    min_road_width: float = 4.0
    min_road_contact: float = 2.0
    # 道路幅員による制限を適用する下限幅員(これ未満は別途扱い)
    road_limit_threshold: float = 12.0
    residential_zones: tuple[str, ...] = DEFAULT_RESIDENTIAL_ZONES

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LandSettings":
        if not data:
            return cls()
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise LandError(f"土地診断の設定に未知の項目があります: {sorted(unknown)}")
        merged = dict(data)
        if "residential_zones" in merged:
            merged["residential_zones"] = tuple(merged["residential_zones"])
        return cls(**merged)


@dataclass
class LandConditions:
    """人が調べて入力する敷地条件。推測して埋めてはいけない。"""

    site_area: float                      # 敷地面積 (㎡)
    zoning: str                           # 用途地域
    building_coverage: float              # 指定建蔽率 (%)
    floor_area_ratio: float               # 指定容積率 (%)
    road_width: float | None = None       # 前面道路幅員 (m)
    road_contact: float | None = None     # 接道長さ (m)
    relaxations: list[str] = field(default_factory=list)   # 適用が確認できた緩和
    note: str = ""

    def validate(self) -> None:
        # 必須項目の欠落は、何が足りないかをまとめて示す。
        # 「調べるべきこと」が一度に分かる方が、実務では手戻りが少ない。
        required = {
            "site_area": "敷地面積(㎡)",
            "zoning": "用途地域",
            "building_coverage": "指定建蔽率(%)",
            "floor_area_ratio": "指定容積率(%)",
        }
        missing = [label for attr, label in required.items() if getattr(self, attr) is None]
        if missing:
            raise LandError(
                "診断に必要な項目が不足しています: "
                + "、".join(missing)
                + "。都市計画情報や役所で確認して入力してください(推測しないこと)。"
            )
        if self.site_area <= 0:
            raise LandError("敷地面積は 0 より大きい値で指定してください")
        if self.zoning not in ZONING_TYPES:
            raise LandError(
                f"不正な用途地域です: {self.zoning} (選択肢: {'/'.join(ZONING_TYPES)})"
            )
        if not 0 < self.building_coverage <= 100:
            raise LandError("建蔽率は 0 より大きく 100 以下の % で指定してください")
        if self.floor_area_ratio <= 0:
            raise LandError("容積率は 0 より大きい % で指定してください")
        if self.road_width is not None and self.road_width < 0:
            raise LandError("前面道路幅員は 0 以上で指定してください")
        if self.road_contact is not None and self.road_contact < 0:
            raise LandError("接道長さは 0 以上で指定してください")
        unknown = set(self.relaxations) - {key for key, _ in RELAXATIONS}
        if unknown:
            raise LandError(
                f"未知の緩和です: {sorted(unknown)} "
                f"(選択肢: {', '.join(key for key, _ in RELAXATIONS)})"
            )

        # int で渡されても "150㎡" / "150.0㎡" と揺れないよう float に揃える。
        self.site_area = float(self.site_area)
        self.building_coverage = float(self.building_coverage)
        self.floor_area_ratio = float(self.floor_area_ratio)
        if self.road_width is not None:
            self.road_width = float(self.road_width)
        if self.road_contact is not None:
            self.road_contact = float(self.road_contact)


def _round(value: float) -> float:
    return round(value, 2)


def diagnose(conditions: LandConditions, settings: LandSettings) -> dict[str, Any]:
    """敷地条件から建てられるボリュームの目安を計算する。"""
    conditions.validate()

    # --- 建蔽率 ---------------------------------------------------------
    coverage = conditions.building_coverage
    applied_relaxations = []
    for key, label in RELAXATIONS:
        if key in conditions.relaxations:
            coverage += 10
            applied_relaxations.append(label)
    coverage = min(coverage, 100)
    building_area = _round(conditions.site_area * coverage / 100)

    # --- 容積率 ---------------------------------------------------------
    designated = conditions.floor_area_ratio
    is_residential = conditions.zoning in settings.residential_zones
    coefficient = (
        settings.road_coefficient_residential
        if is_residential
        else settings.road_coefficient_other
    )

    road_limit: float | None = None
    if conditions.road_width is not None and conditions.road_width < settings.road_limit_threshold:
        road_limit = _round(conditions.road_width * coefficient * 10)

    if road_limit is None:
        applied_far = designated
        far_basis = (
            f"指定容積率 {designated}% を適用。"
            + (
                f"前面道路幅員 {conditions.road_width}m は "
                f"{settings.road_limit_threshold}m 以上のため、幅員による制限は見ていない。"
                if conditions.road_width is not None
                else "前面道路幅員が未入力のため、幅員による制限を計算していない。"
            )
        )
    else:
        applied_far = min(designated, road_limit)
        far_basis = (
            f"指定容積率 {designated}% と、前面道路幅員による制限 "
            f"{conditions.road_width}m × {coefficient}/10 = {road_limit}% の"
            f"小さい方を適用して {applied_far}%。"
            f"(係数 {coefficient}/10 は事務所プロフィールの設定値)"
        )

    total_floor_area = _round(conditions.site_area * applied_far / 100)

    # --- 接道義務 -------------------------------------------------------
    road_check: dict[str, Any] = {"judged": False}
    if conditions.road_width is not None and conditions.road_contact is not None:
        width_ok = conditions.road_width >= settings.min_road_width
        contact_ok = conditions.road_contact >= settings.min_road_contact
        road_check = {
            "judged": True,
            "width_ok": width_ok,
            "contact_ok": contact_ok,
            "passes": width_ok and contact_ok,
            "basis": f"幅員 {conditions.road_width}m(基準 {settings.min_road_width}m 以上)、"
            f"接道長さ {conditions.road_contact}m(基準 {settings.min_road_contact}m 以上)。"
            f"基準値は事務所プロフィールの設定値。",
        }

    # --- 未入力の項目 ---------------------------------------------------
    missing = []
    if conditions.road_width is None:
        missing.append("前面道路幅員(容積率の制限と接道義務の判定に必要)")
    if conditions.road_contact is None:
        missing.append("接道長さ(接道義務の判定に必要)")

    return {
        "site_area": conditions.site_area,
        "zoning": conditions.zoning,
        "building_area_max": building_area,
        "building_coverage_applied": coverage,
        "coverage_relaxations": applied_relaxations,
        "coverage_basis": f"敷地面積 {conditions.site_area}㎡ × 建蔽率 {coverage}% "
        f"= 建築面積 {building_area}㎡。"
        + (
            f"({'、'.join(applied_relaxations)}として +10% ずつ加算。適用可否は行政に要確認)"
            if applied_relaxations
            else "(緩和は適用していない)"
        ),
        "total_floor_area_max": total_floor_area,
        "floor_area_ratio_applied": applied_far,
        "floor_area_ratio_designated": designated,
        "floor_area_ratio_road_limit": road_limit,
        "floor_area_basis": far_basis
        + f" 敷地面積 {conditions.site_area}㎡ × {applied_far}% "
        f"= 延床面積 {total_floor_area}㎡(容積対象)。",
        "road_check": road_check,
        "missing_inputs": missing,
        "required_confirmations": [
            {"item": item, "detail": detail} for item, detail in REQUIRED_CONFIRMATIONS
        ],
        "disclaimer": "これは入力された規制値から機械的に計算した目安であり、"
        "法適合の判断ではない。斜線制限・日影規制・地区計画・各種条例は計算していない。"
        "適用される規制と数値は必ず所管行政庁と都市計画情報で確認すること。"
        "施主に提示する際は、この但し書きを必ず添えること。",
    }
