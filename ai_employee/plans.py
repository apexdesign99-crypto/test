"""過去の住宅プランのデータベース。

事務所が Excel で蓄積した過去プランを読み、敷地条件から似た事例を探す。
「使い方」シートに書かれた次段階——敷地条件から類似プランを提案する——を担う。

    design-data/住宅プランAI_DATABASE.xlsx

**このモジュールはプランを作らない。** 記録されている事例を探して並べるだけ。
似ている事例があることは、その敷地でその案が成立することを意味しない。
"""

from __future__ import annotations

import re
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
        _apply_provenance(record)
    return [Plan(plan_id=k, fields=v) for k, v in sorted(merged.items())]


# ㎡から坪への換算。Excel の数式と同じ係数を使う。
TSUBO_PER_SQM = 1 / 3.305785

# 図面から拾う項目ではなく、こちらで立てる印。充実度の集計からは外す。
_BOOKKEEPING = frozenset({"参考事例", "出典"})

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
    criteria: dict[str, Any],
    limit: int = 3,
    path: Path | None = None,
    include_reference: bool = True,
) -> dict[str, Any]:
    """敷地条件などから似た過去プランを探す。

    ``include_reference=False`` にすると、備考に出典が書かれた行——他社の
    分譲住宅などの参考事例——を除き、自社の設計事例だけで探す。

    似ている事例が見つかることは、その敷地でその案が成立することを意味しない。
    法規・地盤・予算の確認は別途必要。
    """
    plans = load_plans(path)
    excluded = 0
    if not include_reference:
        kept = [p for p in plans if not p.get("参考事例")]
        excluded = len(plans) - len(kept)
        plans = kept
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

    reference_hits = [i["plan_id"] for i in scored[: max(1, limit)]
                      if i["fields"].get("参考事例")]
    caveat = (
        "記録されている過去事例を、指定条件との近さで並べたもの。"
        "似た事例があることは、その敷地でその案が成立することを意味しない。"
        "法規・地盤・予算・施主の要望は別途確認すること。"
        "比較できた項目は matched_on に示す。"
    )
    if reference_hits:
        caveat += (
            "なお " + "、".join(reference_hits) + " は他社・外部の参考事例です"
            "(備考に出典あり)。自社の設計方針とは別物として扱うこと。"
        )
    return {
        "criteria": usable,
        "total_plans": len(plans),
        "comparable": len(scored),
        "excluded_reference": excluded,
        "results": scored[: max(1, limit)],
        "reference_hits": reference_hits,
        "caveat": caveat,
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
            if key in _BOOKKEEPING:
                continue  # 出典の印は図面から拾う項目ではないので数えない
            if value not in (None, ""):
                counts[key] = counts.get(key, 0) + 1

    coverage = {
        key: round(count / len(plans) * 100)
        for key, count in sorted(counts.items(), key=lambda kv: -kv[1])
    }
    # 半数以下しか埋まっていない項目は、検索の主軸には使えない。
    sparse = [key for key, rate in coverage.items() if rate <= 50]

    reference = [p.plan_id for p in plans if p.get("参考事例")]
    note = (
        "埋まっている割合が低い項目は検索の精度に効きません。"
        "特に敷地面積坪・間口m・延床面積坪は類似検索の主軸なので、優先して埋めてください。"
    )
    if reference:
        note += (
            f"また {len(plans)} 件のうち {len(reference)} 件は他社・外部の参考事例です"
            f"(備考に出典あり)。自社の設計事例だけで探すこともできます"
            f"(コマンドは plans --own-only)。"
        )
    return {
        "total": len(plans),
        "reference_cases": reference,
        "coverage": coverage,
        "sparse_fields": sparse,
        "note": note,
    }


# ---------------------------------------------------------------- 出典と区分

# 備考欄の先頭に置く出典表記。これがある行は「他社・外部の参考事例」として扱う。
# 自社の設計事例と他社の分譲住宅を同じ重みで検索に載せると、
# 提案が他社の規格住宅に引きずられる。混ぜないための印。
SOURCE_PREFIX = "出典:"

# 出典を書き込む列。テンプレートの⑥図面ファイルシートにある。
REMARKS_COLUMN = "備考"


def _apply_provenance(record: dict[str, Any]) -> None:
    """備考の出典表記を読んで、自社事例か参考事例かを立てる。

    出典が書かれていない行は自社事例として扱う。事務所が自分の図面から
    拾い出した行に、いちいち「自社」と書かせないため。
    """
    remarks = record.get(REMARKS_COLUMN)
    text = str(remarks).strip() if remarks not in (None, "") else ""
    if text.startswith(SOURCE_PREFIX):
        record["出典"] = text[len(SOURCE_PREFIX):].strip()
        record["参考事例"] = True
    else:
        record["参考事例"] = False


# ------------------------------------------------------------ 書き込みの防具

# 「使い方」シートの指示——顧客名・住所・電話番号を入れない——を機械側で守る。
# 完全な検出はできない。明らかなものを止める関所であって、保証ではない。
_PERSONAL_PATTERNS = (
    (re.compile(r"\d{2,4}-\d{2,4}-\d{3,4}"), "電話番号らしい数字"),
    (re.compile(r"0\d{9,10}"), "電話番号らしい数字"),
    (re.compile(r"〒\s*\d{3}"), "郵便番号"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "メールアドレス"),
    (re.compile(r"\d+\s*丁目|\d+\s*番地|\d+\s*番\d+\s*号"), "住所らしい表記"),
    (re.compile(r"[^\s]{1,10}\s*(様|さま|様邸|邸)\s*$"), "施主名らしい表記"),
)


def _reject_personal_info(column: str, value: Any) -> None:
    """個人情報らしい値を書き込ませない。

    このデータベースは AI の検索・生成に使う。施主の氏名や住所が入ると、
    提案文に紛れ込む経路ができてしまう。入口で止める。
    """
    if not isinstance(value, str):
        return
    for pattern, label in _PERSONAL_PATTERNS:
        if pattern.search(value):
            raise PlanError(
                f"「{column}」の値に{label}が含まれています: {value!r}\n"
                f"「使い方」シートのとおり、顧客名・住所・電話番号は入力できません。"
                f"物件を区別する必要があれば、物件IDだけで管理してください。"
            )


# ------------------------------------------------------------ シートの読み取り

@dataclass
class Column:
    """書き込み先の 1 列。"""

    sheet: str
    index: int  # 1 始まり
    name: str
    formula: bool
    choices: tuple[str, ...] | None

    @property
    def qualified(self) -> str:
        return f"{self.sheet}.{self.name}"


def _choices_by_column(sheet: Any) -> dict[int, tuple[str, ...]]:
    """入力規則のリストを、列番号 -> 選択肢 に直す。"""
    from openpyxl.utils.cell import range_boundaries

    found: dict[int, tuple[str, ...]] = {}
    for validation in sheet.data_validations.dataValidation:
        formula = validation.formula1
        if not isinstance(formula, str) or not formula.startswith('"'):
            continue  # 数値範囲などリスト以外の規則は対象外
        values = tuple(v for v in formula.strip('"').split(",") if v)
        if not values:
            continue
        for cell_range in str(validation.sqref).split():
            bounds = range_boundaries(cell_range)
            if bounds[0] is not None:
                found[bounds[0]] = values
    return found


def _read_schema(workbook: Any) -> dict[str, list[Column]]:
    """列名 -> その名前を持つ全シートの列。

    同じ列名が複数シートにある(②の要望と③の実績など)。
    どちらに書くかは呼び出し側が決める。機械が勝手に選ばない。
    """
    schema: dict[str, list[Column]] = {}
    for sheet in workbook.worksheets:
        if sheet.title.startswith("使い方"):
            continue
        header = [c.value for c in sheet[1]]
        if KEY_COLUMN not in [str(h).strip() if h else "" for h in header]:
            continue
        choices = _choices_by_column(sheet)
        for offset, raw in enumerate(header):
            name = str(raw).strip() if raw not in (None, "") else ""
            if not name or name == KEY_COLUMN:
                continue
            index = offset + 1
            sample = sheet.cell(row=2, column=index).value
            schema.setdefault(name, []).append(Column(
                sheet=sheet.title,
                index=index,
                name=name,
                formula=isinstance(sample, str) and sample.startswith("="),
                choices=choices.get(index),
            ))
    return schema


def describe_schema(path: Path | None = None) -> dict[str, Any]:
    """入力できる列と、選べる値の一覧。拾い出しの前に見るもの。"""
    workbook = _open_for_write(path)
    try:
        schema = _read_schema(workbook)
    finally:
        workbook.close()

    writable: dict[str, list[dict[str, Any]]] = {}
    computed: list[str] = []
    for name, columns in schema.items():
        for column in columns:
            if column.formula:
                computed.append(column.qualified)
                continue
            writable.setdefault(column.sheet, []).append({
                "列": name,
                "選択肢": list(column.choices) if column.choices else None,
                "重複": len([c for c in schema[name] if not c.formula]) > 1,
            })
    return {
        "sheets": writable,
        "computed": sorted(computed),
        "note": "computed の列は Excel の数式が入っています。書き込みません。"
        "重複が True の列は複数シートに同じ名前があります。"
        "「③部屋・面積.LDK帖」のようにシート名を付けて指定してください。",
    }


def _open_for_write(path: Path | None) -> Any:
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - 環境依存
        raise PlanError(
            "openpyxl が必要です。pip install openpyxl を実行してください。"
        ) from None

    source = Path(path or DEFAULT_DB)
    if not source.is_file():
        raise PlanError(f"プランデータベースが見つかりません: {source}")
    warnings.simplefilter("ignore")
    try:
        return openpyxl.load_workbook(source, data_only=False)
    except Exception as exc:  # noqa: BLE001 - 壊れたファイルの理由を見せる
        raise PlanError(f"ファイルを読めません: {type(exc).__name__}: {exc}") from None


def _resolve(name: str, schema: dict[str, list[Column]]) -> Column:
    """指定された列名を、書き込み先 1 つに決める。決まらなければ断る。"""
    if "." in name:
        sheet, _, bare = name.partition(".")
        for column in schema.get(bare, []):
            if column.sheet == sheet:
                return column
        raise PlanError(f"「{sheet}」に「{bare}」という列はありません。")

    columns = [c for c in schema.get(name, []) if not c.formula]
    if not columns:
        if name in schema:
            raise PlanError(
                f"「{name}」は数式の列です。Excel が計算するので書き込めません。"
            )
        raise PlanError(
            f"「{name}」という列はありません。"
            f"plans schema で入力できる列を確認してください。"
        )
    if len(columns) > 1:
        places = "、".join(c.qualified for c in columns)
        raise PlanError(
            f"「{name}」は複数のシートにあります: {places}\n"
            f"どちらの意味かは機械には決められません。"
            f"シート名を付けて指定してください。"
        )
    return columns[0]


def _check_choice(column: Column, value: Any) -> None:
    if column.choices and str(value).strip() not in column.choices:
        raise PlanError(
            f"「{column.qualified}」に入れられるのは "
            f"{'、'.join(column.choices)} のいずれかです。"
            f"指定された値: {value!r}"
        )


def _find_row(sheet: Any, plan_id: str) -> int | None:
    for row in range(2, sheet.max_row + 1):
        cell = sheet.cell(row=row, column=1).value
        if cell is not None and str(cell).strip() == plan_id:
            return row
    return None


def _first_blank_row(sheet: Any) -> int:
    for row in range(2, sheet.max_row + 2):
        if sheet.cell(row=row, column=1).value in (None, ""):
            return row
    return sheet.max_row + 1


def record_plan(
    plan_id: str,
    fields: dict[str, Any],
    source: str | None = None,
    path: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """1 物件を書き込む。図面から読み取れたものだけを渡すこと。

    ``source`` を渡すと、その物件は他社・外部の参考事例として印を付ける。
    自社の図面から拾ったものには渡さない。

    読み取れなかった項目は渡さないこと。埋めるために推測した値を入れると、
    後で類似検索がその推測を「事例」として返す。空欄のほうが害がない。
    """
    plan_id = str(plan_id).strip()
    if not plan_id:
        raise PlanError("物件IDが空です。P0001 のような ID を付けてください。")
    _reject_personal_info("物件ID", plan_id)

    usable = {k: v for k, v in fields.items() if v not in (None, "")}
    if not usable and not source:
        raise PlanError(
            f"{plan_id} に書き込む値がありません。"
            f"図面から読み取れた項目だけでよいので、少なくとも 1 つ渡してください。"
        )

    workbook = _open_for_write(path)
    schema = _read_schema(workbook)

    # 先に全部を検査する。途中で撥ねられて半分だけ書かれた行を残さない。
    resolved: list[tuple[Column, Any]] = []
    for name, value in usable.items():
        column = _resolve(name, schema)
        if column.formula:
            raise PlanError(
                f"「{column.qualified}」は数式の列です。Excel が計算するので書き込めません。"
            )
        _reject_personal_info(column.qualified, value)
        _check_choice(column, value)
        resolved.append((column, value))

    if source is not None:
        remarks = _resolve(REMARKS_COLUMN, schema)
        if any(column.qualified == remarks.qualified for column, _ in resolved):
            # 出典は備考欄に書く。両方渡されると片方が黙って消える。
            raise PlanError(
                f"source と「{REMARKS_COLUMN}」を同時に指定できません。"
                f"出典は備考欄に書き込むため、どちらか一方にしてください。"
            )
        _reject_personal_info(REMARKS_COLUMN, source)
        resolved.append((remarks, f"{SOURCE_PREFIX} {source}".strip()))

    # 書き込む先のシートを決める。既にある行はそこへ、無ければ全シートの同じ位置へ。
    touched = {column.sheet for column, _ in resolved}
    rows: dict[str, int] = {}
    existing = []
    for title in sorted(touched):
        sheet = workbook[title]
        found = _find_row(sheet, plan_id)
        if found is not None:
            existing.append(title)
        rows[title] = found if found is not None else _first_blank_row(sheet)

    if existing and not replace:
        raise PlanError(
            f"{plan_id} は既に登録されています({'、'.join(existing)})。"
            f"上書きしてよければ replace=True を指定してください。"
        )

    written: list[str] = []
    for column, value in resolved:
        sheet = workbook[column.sheet]
        row = rows[column.sheet]
        sheet.cell(row=row, column=1).value = plan_id
        sheet.cell(row=row, column=column.index).value = value
        written.append(column.qualified)

    destination = Path(path or DEFAULT_DB)
    workbook.save(destination)
    workbook.close()

    return {
        "plan_id": plan_id,
        "written": sorted(written),
        "rows": rows,
        "reference_case": source is not None,
        "note": "坪・収納合計・総合評価・坪単価は Excel の数式が計算します。"
        "Excel か LibreOffice で一度開くまで、それらの列は空のままです。"
        "(plans.py は坪だけ㎡から補って読みます。)",
    }
