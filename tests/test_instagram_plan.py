"""Instagram 運用計画のテスト。

運用が止まるのは「許諾を取る前に企画が進む」「素材がないまま原稿だけ出来る」
「予定日を過ぎて放置」のどれか。そこが止まることを確認する。
"""

import json
from datetime import date, timedelta

import pytest

from ai_employee.company import ProjectLedger
from ai_employee.instagram_plan import (
    PLAN_MIXES,
    POST_STATUSES,
    InstagramPlan,
    PlanError,
    month_range,
)
from ai_employee.workspace import now


@pytest.fixture
def plan(tmp_path) -> InstagramPlan:
    return InstagramPlan(tmp_path)


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


# ---------------------------------------------------------------- 月の範囲


@pytest.mark.parametrize(
    "ym,first,last",
    [("2026-09", "2026-09-01", "2026-09-30"),
     ("2026-12", "2026-12-01", "2026-12-31"),
     ("2028-02", "2028-02-01", "2028-02-29")],  # 閏年
)
def test_月の範囲を正しく出す(ym, first, last):
    assert month_range(ym) == (first, last)


@pytest.mark.parametrize("bad", ["2026/09", "2026-13", "九月", ""])
def test_不正な年月は拒否される(bad):
    with pytest.raises(PlanError, match="YYYY-MM"):
        month_range(bad)


# ---------------------------------------------------------------- 下書き


def test_配分どおりの本数を作る(plan):
    created = plan.draft_month("2026-09", "standard")
    assert len(created) == sum(PLAN_MIXES["standard"]["mix"].values())
    kinds = [p["format"] for p in created]
    for post_format, count in PLAN_MIXES["standard"]["mix"].items():
        assert kinds.count(post_format) == count


def test_月内に収まり同じ型が続かない(plan):
    created = plan.draft_month("2026-09", "standard")
    first, last = month_range("2026-09")
    assert all(first <= p["scheduled_date"] <= last for p in created)
    kinds = [p["format"] for p in created]
    # 施工事例だけが連続で並ぶと、検討初期層に届かない
    assert not any(a == b for a, b in zip(kinds, kinds[1:]))


def test_予定日は早い順に並ぶ(plan):
    created = plan.draft_month("2026-09")
    dates = [p["scheduled_date"] for p in created]
    assert dates == sorted(dates)


def test_二重に作らない(plan):
    """作り直しで題材や進行状況を消さないため。"""
    plan.draft_month("2026-09")
    with pytest.raises(PlanError, match="既に計画があります"):
        plan.draft_month("2026-09")


def test_不正な配分は拒否される(plan):
    with pytest.raises(PlanError, match="不正な配分"):
        plan.draft_month("2026-09", "バズ狙い")


def test_全ての配分が作成できる(plan, tmp_path):
    for index, mix in enumerate(PLAN_MIXES):
        InstagramPlan(tmp_path / str(index)).draft_month("2026-09", mix)


# ------------------------------------------------------------ 許諾の壁


def test_許諾のない案件は計画に入れられない(plan, ledger):
    """許諾を取る前に企画を進めてしまう事故を防ぐ。"""
    project = ledger.add("K様邸 新築", "K様", "戸建住宅")
    with pytest.raises(PlanError, match="掲載許諾が「未確認」"):
        plan.add("2026-09-10", "works", project_id=project["id"], ledger=ledger)


def test_不可の案件も入れられない(plan, ledger):
    project = ledger.add("K様邸 新築")
    ledger.record_consent(project["id"], "不可")
    with pytest.raises(PlanError, match="掲載許諾が「不可」"):
        plan.add("2026-09-10", "works", project_id=project["id"], ledger=ledger)


def test_許諾済みなら入り条件も引き継ぐ(plan, ledger):
    project = ledger.add("K様邸 新築")
    ledger.record_consent(project["id"], "条件付き", "施主名は伏せる")
    post = plan.add("2026-09-10", "works", "光の回る家",
                    project_id=project["id"], ledger=ledger, by="marke")
    assert post["consent_conditions"] == "施主名は伏せる"
    assert post["status"] == "企画"


def test_案件紐付けには台帳が要る(plan):
    with pytest.raises(PlanError, match="案件台帳が必要"):
        plan.add("2026-09-10", "works", project_id="abc")


# ------------------------------------------------------------ 素材の壁


def test_素材がなければ原稿済にできない(plan):
    """写真がないまま原稿だけ進めると、予定日に出せず運用が止まる。"""
    post = plan.add("2026-09-10", "works", "光の回る家")
    with pytest.raises(PlanError, match="素材が揃っていない") as exc:
        plan.update(post["id"], status="原稿済")
    assert "外観写真" in str(exc.value)   # 何が必要かを示す


def test_素材がなければ投稿済にもできない(plan):
    post = plan.add("2026-09-10", "works")
    with pytest.raises(PlanError, match="素材が揃っていない"):
        plan.update(post["id"], status="投稿済")


def test_素材を揃えれば進められる(plan):
    post = plan.add("2026-09-10", "works")
    assert plan.update(post["id"], status="原稿済", assets_ready=True)["status"] == "原稿済"


def test_素材未確認でも素材待ちにはできる(plan):
    post = plan.add("2026-09-10", "works")
    assert plan.update(post["id"], status="素材待ち")["status"] == "素材待ち"


# ---------------------------------------------------------------- 更新


def test_不正な状態と日付は拒否される(plan):
    post = plan.add("2026-09-10", "works")
    with pytest.raises(PlanError, match="不正な状態"):
        plan.update(post["id"], status="バズった")
    with pytest.raises(PlanError, match="予定日の形式"):
        plan.update(post["id"], scheduled_date="9月10日")


def test_不正な型と日付は追加時に弾かれる(plan):
    with pytest.raises(PlanError, match="不正な投稿の型"):
        plan.add("2026-09-10", "リール")
    with pytest.raises(PlanError, match="YYYY-MM-DD"):
        plan.add("9月10日", "works")


def test_存在しない投稿の操作はエラー(plan):
    with pytest.raises(PlanError, match="見つかりません"):
        plan.update("deadbeef", status="企画")
    with pytest.raises(PlanError, match="見つかりません"):
        plan.delete("deadbeef")


def test_削除できる(plan):
    post = plan.add("2026-09-10", "works")
    plan.delete(post["id"])
    assert plan.list("2026-09") == []


def test_月と状態で絞れる(plan):
    plan.add("2026-09-10", "works")
    other = plan.add("2026-10-10", "knowledge")
    plan.update(other["id"], status="見送り")
    assert len(plan.list("2026-09")) == 1
    assert len(plan.list("2026-10", status="見送り")) == 1
    assert plan.list("2026-09", status="見送り") == []


# ---------------------------------------------------------------- 抜けの検出


def test_予定日を過ぎた未投稿を検出する(plan):
    past = (now().date() - timedelta(days=3)).isoformat()
    late = plan.add(past, "works", "遅れている")
    plan.add((now().date() + timedelta(days=10)).isoformat(), "knowledge", "これから")
    month = now().strftime("%Y-%m")

    gaps = plan.gaps(month)
    overdue_ids = [p["id"] for p in gaps["overdue"]]
    assert late["id"] in overdue_ids


def test_投稿済と見送りは超過に数えない(plan):
    past = (now().date() - timedelta(days=3)).isoformat()
    done = plan.add(past, "works")
    skipped = plan.add(past, "knowledge")
    plan.update(done["id"], status="投稿済", assets_ready=True)
    plan.update(skipped["id"], status="見送り")
    assert plan.gaps(now().strftime("%Y-%m"))["overdue"] == []


def test_素材待ちと題材未定を検出する(plan):
    waiting = plan.add("2026-09-10", "works", "題材あり")
    plan.update(waiting["id"], status="素材待ち")
    plan.add("2026-09-20", "knowledge")   # 題材なし

    gaps = plan.gaps("2026-09")
    assert [p["id"] for p in gaps["waiting_assets"]] == [waiting["id"]]
    assert len(gaps["no_title"]) == 1


def test_目標本数への不足を出す(plan):
    plan.add("2026-09-10", "works")
    gaps = plan.gaps("2026-09", cadence=6)
    assert gaps["planned"] == 1
    assert gaps["shortfall"] == 5


def test_見送りは計画本数に数えない(plan):
    post = plan.add("2026-09-10", "works")
    plan.update(post["id"], status="見送り")
    assert plan.gaps("2026-09", cadence=6)["planned"] == 0


def test_目標未設定なら不足を出さない(plan):
    plan.add("2026-09-10", "works")
    gaps = plan.gaps("2026-09")
    assert gaps["cadence"] is None
    assert gaps["shortfall"] == 0


def test_計画が空でも落ちない(plan):
    gaps = plan.gaps("2026-09", cadence=6)
    assert gaps["planned"] == 0 and gaps["shortfall"] == 6
    assert gaps["overdue"] == []


def test_全ての状態が使える(plan):
    post = plan.add("2026-09-10", "works")
    plan.update(post["id"], assets_ready=True)
    for status in POST_STATUSES:
        assert plan.update(post["id"], status=status)["status"] == status
