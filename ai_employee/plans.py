"""過去の住宅プランのデータベース。

事務所が Excel で蓄積した過去プランを読み、敷地条件から似た事例を探す。
「使い方」シートに書かれた次段階——敷地条件から類似プランを提案する——を担う。

    design-data/住宅プランAI_DATABASE.xlsx

**このモジュールはプランを作らない。** 記録されている事例を探して並べるだけ。
似ている事例があることは、その敷地でその案が成立することを意味しない。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# データベースの既定の場所。
DEFAULT_DB = Path("design-data/住宅プランAI_DATABASE.xlsx")

# 物件を串刺しにする列。全シートに共通。
KEY_COLUMN = "物件ID"

# 類似度の重み。敷地の当てはまりを最優先する。
# 事務所の設計方針に合わせて変えてよい。
WEIGHTS = {
    "敷地面積坪": 3.0,
    "間口m": 2.0,
    "延床面積坪": 2.0,
    "前面道路方向": 1.5,
    "敷地形状": 1.5,
    "階数": 1.0,
    "家族人数": 1.0,
}

# 数値項目の「これだけ違えば別物」とみなす幅。正規化に使う。
SCALES = {
    "敷地面積坪": 20.0,
    "間口m": 4.0,
    "延床面積坪": 15.0,
    "階数": 1.0,
    "家族人数": 2.0,
}


class PlanError(RuntimeError):
    """プランデータベースの読み込みに失敗した。"""


@dataclass
class Plan:
    """1 物件ぶんの記録。全シートを物件 ID で束ねたもの。"""

    plan_id: str
    fields: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)

    def filled_count(self) -> int:
        return sum(1 for v in self.fields.values() if v not in (None, ""))


def load_plans(path: Path | None = None) -> list[Plan]:
    """全シートを読み、物件 ID で束ねて返す。

    空行(物件 ID がない行)は読み飛ばす。テンプレートは 500 行の空行を持つため。
    """
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - 環境依存
        raise PlanError(
            "openpyxl が必要です。pip install openpyxl を実行してください。"
        ) from None

    source = Path(path or DEFAULT_DB)
    if not source.is_file():
        raise PlanError(
            f"プランデータベースが見つかりません: {source}\n"
            f"design-data/ に住宅プランAI_DATABASE.xlsx を置いてください。"
        )

    # 条件付き書式などの警告は読み取り結果に影響しないので黙らせる。
    # 毎回画面に出ると、本当の問題が埋もれる。
    warnings.simplefilter("ignore")
    try:
        workbook = openpyxl.load_workbook(source, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - 壊れたファイルの理由を見せる
        raise PlanError(f"ファイルを読めません: {type(exc).__name__}: {exc}") from None

    merged: dict[str, dict[str, Any]] = {}
    for sheet in workbook.worksheets:
        if sheet.title.startswith("使い方"):
            continue
        rows = sheet.iter_rows(values_only=True)
        try:
            header = [str(h).strip() if h is not None else "" for h in next(rows)]
        except StopIteration:
            continue
        if KEY_COLUMN not in header:
            continue
        key_index = header.index(KEY_COLUMN)

        for row in rows:
            if key_index >= len(row):
                continue
            plan_id = row[key_index]
            if plan_id in (None, ""):
                continue
            record = merged.setdefault(str(plan_id).strip(), {})
            for index, name in enumerate(header):
                if not name or index == key_index or index >= len(row):
                    continue
                value = row[index]
                if value not in (None, ""):
                    # 同名列が複数シートにある場合は先に読んだ方を残す
                    record.setdefault(name, value)
    workbook.close()

    for record in merged.values():
        _derive(record)
    return [Plan(plan_id=k, fields=v) for k, v in sorted(merged.items())]


# ㎡から坪への換算。Excel の数式と同じ係数を使う。
TSUBO_PER_SQM = 1 / 3.305785

# (導出する列, 元の列) — 数式列のキャッシュ値が無いときの保険。
_DERIVED = (("延床面積坪", "延床面積㎡"), ("敷地面積坪", "敷地面積㎡"))


def _derive(record: dict[str, Any]) -> None:
    """数式列が空でも、元の値から補える項目を埋める。

    openpyxl で書き出した直後のファイルは数式のキャッシュ値を持たないため、
    坪の列が空で読めることがある。類似検索の主軸なので落とさない。
    """
    for target, source in _DERIVED:
        if record.get(target) in (None, ""):
            base = _number(record.get(source))
            if base is not None:
                record[target] = round(base * TSUBO_PER_SQM, 1)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _distance(plan: Plan, criteria: dict[str, Any]) -> tuple[float, list[str]] | None:
    """条件との隔たりを 0(完全一致)からの数値で返す。

    比較できる項目が 1 つもなければ None(候補にしない)。
    """
    total = 0.0
    weight_sum = 0.0
    matched: list[str] = []

    for key, wanted in criteria.items():
        if wanted in (None, ""):
            continue
        actual = plan.get(key)
        if actual in (None, ""):
            continue
        weight = WEIGHTS.get(key, 1.0)

        wanted_number, actual_number = _number(wanted), _number(actual)
        if wanted_number is not None and actual_number is not None:
            scale = SCALES.get(key, max(abs(wanted_number), 1.0))
            gap = min(abs(actual_number - wanted_number) / scale, 1.0)
        else:
            gap = 0.0 if str(actual).strip() == str(wanted).strip() else 1.0

        total += gap * weight
        weight_sum += weight
        if gap < 0.25:
            matched.append(key)

    if weight_sum == 0:
        return None
    return total / weight_sum, matched


def search_similar(
    criteria: dict[str, Any], limit: int = 3, path: Path | None = None
) -> dict[str, Any]:
    """敷地条件などから似た過去プランを探す。

    似ている事例が見つかることは、その敷地でその案が成立することを意味しない。
    法規・地盤・予算の確認は別途必要。
    """
    plans = load_plans(path)
    usable = {k: v for k, v in criteria.items() if v not in (None, "")}
    if not usable:
        raise PlanError(
            "検索条件が指定されていません。"
            f"敷地面積坪・間口m・前面道路方向・家族人数などを指定してください。"
        )

    scored = []
    for plan in plans:
        result = _distance(plan, usable)
        if result is None:
            continue
        distance, matched = result
        scored.append({
            "plan_id": plan.plan_id,
            "similarity": round((1 - distance) * 100, 1),
            "matched_on": matched,
            "fields": plan.fields,
        })
    scored.sort(key=lambda item: -item["similarity"])

    return {
        "criteria": usable,
        "total_plans": len(plans),
        "comparable": len(scored),
        "results": scored[: max(1, limit)],
        "caveat": "記録されている過去事例を、指定条件との近さで並べたもの。"
        "似た事例があることは、その敷地でその案が成立することを意味しない。"
        "法規・地盤・予算・施主の要望は別途確認すること。"
        "比較できた項目は matched_on に示す。",
    }


def stats(path: Path | None = None) -> dict[str, Any]:
    """データベースの充実度。どの項目がどれだけ埋まっているか。"""
    plans = load_plans(path)
    if not plans:
        return {
            "total": 0,
            "coverage": {},
            "note": "登録されているプランがありません。"
            "過去の図面・見積・契約書から拾い出して入力してください。"
            "「使い方」シートのとおり、まず 10〜20 件でルールを確認するのが確実です。",
        }

    counts: dict[str, int] = {}
    for plan in plans:
        for key, value in plan.fields.items():
            if value not in (None, ""):
                counts[key] = counts.get(key, 0) + 1

    coverage = {
        key: round(count / len(plans) * 100)
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1])
    }
    # 半数以下しか埋まっていない項目は、検索の主軸には使えない。
    sparse = [key for key, rate in coverage.items() if rate <= 50]
    return {
        "total": len(plans),
        "coverage": coverage,
        "sparse_fields": sparse,
        "note": "埋まっている割合が低い項目は検索の精度に効きません。"
        "特に敷地面積坪・間口m・延床面積坪は類似検索の主軸なので、優先して埋めてください。",
    }
