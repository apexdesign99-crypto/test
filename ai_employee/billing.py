"""出来高払いの請求計画。

設計事務所の設計監理料は、契約時・基本設計完了時・実施設計完了時・引渡時、
というように段階払いになることが多い。ここでは契約金額を配分割合で割り付ける
計算だけを扱う(台帳への保存は company.py 側)。

金額は円単位の整数で扱う。端数は最終回で吸収し、合計が契約金額と必ず一致するようにする。
"""

from __future__ import annotations

from typing import Any

# 請求の進行状態。
BILLING_STATUSES = ("未請求", "請求済", "入金済")

# 入金遅延とみなす既定の日数(請求日から起算)。
DEFAULT_PAYMENT_TERM_DAYS = 30

# 出来高払いの一例。事務所の契約実態に合わせて office コマンドで設定する。
# (表示名, 配分割合 %, この案件ステージに達したら請求できる)
EXAMPLE_SCHEDULE: tuple[tuple[str, int, str], ...] = (
    ("契約金", 30, "設計契約"),
    ("基本設計完了時", 30, "基本設計"),
    ("実施設計完了時", 30, "実施設計"),
    ("引渡時", 10, "竣工"),
)


class BillingError(RuntimeError):
    """請求計画の組み立てに失敗した。"""


def validate_schedule(schedule: list[dict[str, Any]]) -> None:
    """配分割合の合計が 100% になっているかを確かめる。"""
    if not schedule:
        raise BillingError(
            "請求スケジュールが事務所プロフィールに未設定です。"
            "office コマンドの --billing-schedule で設定してください"
            "(例: 契約金:30:設計契約,基本設計完了:30:基本設計,"
            "実施設計完了:30:実施設計,引渡:10:竣工)。"
        )
    total = sum(entry["ratio"] for entry in schedule)
    if total != 100:
        raise BillingError(
            f"配分割合の合計が {total}% です。100% になるように設定してください。"
        )


def build_plan(
    contract_amount: int, schedule: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """契約金額を配分割合で割り付ける。

    各回は円未満を切り捨て、生じた端数はすべて最終回に寄せる。
    こうすることで、各回の合計が契約金額と必ず一致する。
    """
    validate_schedule(schedule)
    if contract_amount <= 0:
        raise BillingError("契約金額は 1 円以上で指定してください")

    plan: list[dict[str, Any]] = []
    allocated = 0
    for index, entry in enumerate(schedule, 1):
        amount = contract_amount * entry["ratio"] // 100
        plan.append(
            {
                "id": f"m{index}",
                "label": entry["label"],
                "ratio": entry["ratio"],
                "trigger_stage": entry["stage"],
                "amount": amount,
                "status": "未請求",
                "invoiced_at": None,
                "paid_at": None,
                "note": "",
            }
        )
        allocated += amount

    remainder = contract_amount - allocated
    if remainder:
        # 端数は最終回に寄せる。合計を契約金額に一致させるため。
        plan[-1]["amount"] += remainder
        plan[-1]["note"] = f"端数 {remainder:,} 円を調整"

    return plan


def totals(plan: list[dict[str, Any]]) -> dict[str, int]:
    """請求計画の合計・請求済・入金済・未入金を集計する。"""
    result = {"total": 0, "invoiced": 0, "paid": 0, "unbilled": 0, "outstanding": 0}
    for item in plan:
        amount = item["amount"]
        result["total"] += amount
        if item["status"] == "入金済":
            result["paid"] += amount
            result["invoiced"] += amount
        elif item["status"] == "請求済":
            result["invoiced"] += amount
            result["outstanding"] += amount
        else:
            result["unbilled"] += amount
    return result


def with_tax(amount: int, tax_rate: float | None) -> dict[str, Any]:
    """税込金額を返す。税率が未設定なら税込を出さない。

    適用税率は事務所が確認して設定するもので、こちらで推測しない。
    """
    if tax_rate is None:
        return {
            "excluding_tax": amount,
            "tax": None,
            "including_tax": None,
            "note": "消費税率が事務所プロフィールに未設定のため、税込金額は算出していない。",
        }
    tax = int(amount * tax_rate / 100)
    return {
        "excluding_tax": amount,
        "tax": tax,
        "including_tax": amount + tax,
        "tax_rate": tax_rate,
        "note": f"税率 {tax_rate}% は事務所プロフィールの設定値。適用税率は事務所で確認すること。",
    }
