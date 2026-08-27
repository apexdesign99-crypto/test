"""事務担当の要——請求計画と、請求漏れ・入金遅延の検出。

金額を扱うので、合計が契約金額と一致することと、
実績(請求済・入金済)が事故で消えないことを重点的に確認する。
"""

import json
from datetime import timedelta

import pytest

from ai_employee.billing import (
    EXAMPLE_SCHEDULE,
    BillingError,
    build_plan,
    totals,
    validate_schedule,
    with_tax,
)
from ai_employee.company import CompanyError, OfficeProfile, ProjectLedger
from ai_employee.workspace import now

SCHEDULE = [{"label": l, "ratio": r, "stage": s} for l, r, s in EXAMPLE_SCHEDULE]


@pytest.fixture
def office(tmp_path) -> OfficeProfile:
    profile = OfficeProfile(name="A設計", billing_schedule=list(SCHEDULE), tax_rate=10)
    profile.save(tmp_path)
    return profile


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


@pytest.fixture
def project(ledger) -> dict:
    return ledger.add("田中邸 新築", "田中様", "戸建住宅", by="shukyaku")


# ------------------------------------------------------------ 金額の割り付け


@pytest.mark.parametrize("amount", [4_567_890, 1_000_000, 3_333_333, 101, 7])
def test_各回の合計は契約金額と必ず一致する(amount):
    """端数処理で 1 円でもずれると請求書が合わなくなる。"""
    plan = build_plan(amount, SCHEDULE)
    assert sum(m["amount"] for m in plan) == amount


def test_端数は最終回に寄せる():
    """1,000,001 円は 30%/30%/30%/10% で割ると 1 円余る。"""
    plan = build_plan(1_000_001, SCHEDULE)
    assert [m["amount"] for m in plan] == [300_000, 300_000, 300_000, 100_001]
    assert "端数 1 円を調整" in plan[-1]["note"]
    assert sum(m["amount"] for m in plan) == 1_000_001


def test_端数がなければ調整の注記も付かない():
    plan = build_plan(1_000_000, SCHEDULE)
    assert [m["amount"] for m in plan] == [300_000, 300_000, 300_000, 100_000]
    assert plan[-1]["note"] == ""


def test_各回に請求条件のステージが付く():
    plan = build_plan(1_000_000, SCHEDULE)
    assert [m["trigger_stage"] for m in plan] == [
        "設計契約", "基本設計", "実施設計", "竣工"
    ]
    assert all(m["status"] == "未請求" for m in plan)


def test_配分の合計が100でなければ拒否される():
    with pytest.raises(BillingError, match="合計が 90%"):
        validate_schedule([{"label": "a", "ratio": 90, "stage": "竣工"}])


def test_スケジュール未設定は設定方法つきで拒否される():
    with pytest.raises(BillingError, match="未設定"):
        build_plan(1_000_000, [])


@pytest.mark.parametrize("amount", [0, -1])
def test_契約金額が0以下なら拒否される(amount):
    with pytest.raises(BillingError, match="1 円以上"):
        build_plan(amount, SCHEDULE)


def test_合計の集計(project):
    plan = build_plan(1_000_000, SCHEDULE)
    plan[0]["status"] = "入金済"
    plan[1]["status"] = "請求済"
    assert totals(plan) == {
        "total": 1_000_000,
        "invoiced": 600_000,   # 入金済も請求済に含む
        "paid": 300_000,
        "unbilled": 400_000,
        "outstanding": 300_000,
    }


# ---------------------------------------------------------------- 消費税


def test_税率未設定なら税込を出さない():
    result = with_tax(1_000_000, None)
    assert result["including_tax"] is None
    assert "未設定" in result["note"]


def test_税率設定済みなら税込を出す():
    result = with_tax(1_000_000, 10)
    assert result == {
        "excluding_tax": 1_000_000,
        "tax": 100_000,
        "including_tax": 1_100_000,
        "tax_rate": 10,
        "note": result["note"],
    }
    assert "事務所で確認" in result["note"]


# ------------------------------------------------------------ 台帳への反映


def test_請求計画を作ると履歴に残る(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    status = ledger.billing_status(project["id"])
    assert status["contract_amount"] == 1_000_000
    assert len(status["plan"]) == 4
    assert "請求計画を作成" in ledger.get(project["id"])["history"][-1]["entry"]


def test_請求計画未作成なら未設定と分かる(ledger, project):
    status = ledger.billing_status(project["id"])
    assert status["configured"] is False
    assert status["plan"] == []


def test_請求済の実績があれば作り直せない(ledger, project, office):
    """作り直しで請求・入金の実績が消えるのを防ぐ。"""
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update_billing(project["id"], "m1", status="請求済", by="jimu")
    with pytest.raises(CompanyError, match="請求済/入金済の回が 1 件"):
        ledger.setup_billing(project["id"], 2_000_000, office, by="jimu")


def test_未請求だけなら作り直せる(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.setup_billing(project["id"], 2_000_000, office, by="jimu")
    assert ledger.billing_status(project["id"])["contract_amount"] == 2_000_000


def test_請求済にすると請求日が入る(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    milestone = ledger.update_billing(project["id"], "m1", status="請求済", by="jimu")
    assert milestone["invoiced_at"] is not None
    assert milestone["paid_at"] is None


def test_入金済にすると請求日も埋まる(ledger, project, office):
    """請求を飛ばして入金になることはないため。"""
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    milestone = ledger.update_billing(project["id"], "m1", status="入金済", by="jimu")
    assert milestone["invoiced_at"] is not None
    assert milestone["paid_at"] is not None


def test_未請求に戻すと日付が消える(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update_billing(project["id"], "m1", status="入金済", by="jimu")
    milestone = ledger.update_billing(project["id"], "m1", status="未請求", by="jimu")
    assert milestone["invoiced_at"] is None
    assert milestone["paid_at"] is None


def test_金額の修正は履歴に差分が残る(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update_billing(project["id"], "m1", amount=350_000, by="jimu")
    entry = ledger.get(project["id"])["history"][-1]["entry"]
    assert "300,000 → 350,000 円" in entry


def test_変更内容がなければ拒否される(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    with pytest.raises(CompanyError, match="変更内容がありません"):
        ledger.update_billing(project["id"], "m1", by="jimu")


def test_不正な請求状態と金額は拒否される(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    with pytest.raises(CompanyError, match="不正な請求状態"):
        ledger.update_billing(project["id"], "m1", status="たぶん入金")
    with pytest.raises(CompanyError, match="1 円以上"):
        ledger.update_billing(project["id"], "m1", amount=0)


def test_存在しない回の更新は状況つきで拒否される(ledger, project, office):
    with pytest.raises(CompanyError, match="請求計画が未作成"):
        ledger.update_billing(project["id"], "m1")
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    with pytest.raises(CompanyError, match="請求の回が見つかりません"):
        ledger.update_billing(project["id"], "m9")


# -------------------------------------------------- 請求漏れ・入金遅延


def test_ステージが請求条件に達すると請求漏れになる(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    assert ledger.billing_alerts()["unbilled"] == []  # まだ反響

    ledger.update(project["id"], "実施設計に着手", stage="実施設計", by="bim")
    unbilled = ledger.billing_alerts()["unbilled"]
    # 設計契約・基本設計・実施設計の 3 回が請求できる状態
    assert [a["label"] for a in unbilled] == ["契約金", "基本設計完了時", "実施設計完了時"]
    assert "実施設計" in unbilled[0]["reason"]


def test_請求済の回は請求漏れに数えない(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update(project["id"], "契約", stage="設計契約", by="eigyo")
    ledger.update_billing(project["id"], "m1", status="請求済", by="jimu")
    assert ledger.billing_alerts()["unbilled"] == []


def test_失注案件は請求漏れに数えない(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update(project["id"], "他社へ", stage="設計契約", status="lost", by="eigyo")
    assert ledger.billing_alerts()["unbilled"] == []


def test_請求漏れは金額の大きい順に並ぶ(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update(project["id"], "竣工", stage="竣工", by="bim")
    amounts = [a["amount"] for a in ledger.billing_alerts()["unbilled"]]
    assert amounts == sorted(amounts, reverse=True)


def age_invoice(ledger, project_id, milestone_id, days):
    """請求日を過去にずらす(入金遅延の再現)。"""
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    for record in data:
        if record["id"] != project_id:
            continue
        for milestone in record["billing"]["plan"]:
            if milestone["id"] == milestone_id:
                milestone["invoiced_at"] = (now() - timedelta(days=days)).isoformat(
                    timespec="seconds"
                )
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_支払期日を過ぎた請求は入金遅延になる(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update_billing(project["id"], "m1", status="請求済", by="jimu")
    assert ledger.billing_alerts(30)["overdue"] == []

    age_invoice(ledger, project["id"], "m1", 45)
    overdue = ledger.billing_alerts(30)["overdue"]
    assert len(overdue) == 1
    assert overdue[0]["amount"] == 300_000
    assert ledger.billing_alerts(60)["overdue"] == []


def test_入金済は遅延に数えない(ledger, project, office):
    ledger.setup_billing(project["id"], 1_000_000, office, by="jimu")
    ledger.update_billing(project["id"], "m1", status="入金済", by="jimu")
    age_invoice(ledger, project["id"], "m1", 90)
    assert ledger.billing_alerts(30)["overdue"] == []


def test_負の支払期日は拒否される(ledger):
    with pytest.raises(CompanyError, match="0 以上"):
        ledger.billing_alerts(-1)


# ---------------------------------------------------------------- 横断集計


def test_全案件を未入金の多い順に集計する(ledger, office):
    small = ledger.add("小さい案件")
    big = ledger.add("大きい案件")
    ledger.setup_billing(small["id"], 1_000_000, office)
    ledger.setup_billing(big["id"], 5_000_000, office)
    ledger.update_billing(small["id"], "m1", status="請求済")
    ledger.update_billing(big["id"], "m1", status="請求済")

    overview = ledger.billing_overview()
    assert [r["project_name"] for r in overview["projects"]] == ["大きい案件", "小さい案件"]
    assert overview["totals"]["total"] == 6_000_000
    assert overview["totals"]["outstanding"] == 1_800_000


def test_請求計画のない案件は集計に出ない(ledger, project):
    assert ledger.billing_overview()["projects"] == []
