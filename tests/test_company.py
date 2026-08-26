import pytest

from ai_employee.company import CompanyError, ProjectLedger


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


def test_反響を案件として起こせる(ledger):
    p = ledger.add("田中邸 新築", "田中様", "戸建住宅", source="HP問い合わせ", by="shukyaku")
    assert p["stage"] == "反響"
    assert p["status"] == "active"
    assert p["history"][0]["by"] == "shukyaku"
    assert ledger.get(p["id"])["name"] == "田中邸 新築"


def test_案件名は必須(ledger):
    with pytest.raises(CompanyError, match="案件名は必須"):
        ledger.add("   ")


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"stage": "契約直前"}, "不正なステージ"),
        ({"kind": "宇宙ステーション"}, "不正な用途種別"),
    ],
)
def test_不正なステージや用途は拒否される(ledger, kwargs, message):
    with pytest.raises(CompanyError, match=message):
        ledger.add("A邸", **kwargs)


def test_更新は差分つきで履歴に残る(ledger):
    p = ledger.add("田中邸 新築", by="shukyaku")
    updated = ledger.update(
        p["id"], "初回相談を実施", by="eigyo", stage="初回相談", next_action="現地調査の日程調整"
    )
    assert updated["stage"] == "初回相談"
    last = updated["history"][-1]
    assert last["by"] == "eigyo"
    assert "初回相談を実施" in last["entry"]
    assert "反響 → 初回相談" in last["entry"]  # 誰が何を変えたか追える


def test_更新理由がなければ拒否される(ledger):
    """台帳は他部署が読む唯一の記録なので、無言の更新を許さない。"""
    p = ledger.add("田中邸 新築")
    with pytest.raises(CompanyError, match="更新理由"):
        ledger.update(p["id"], "  ", stage="見積")


def test_更新できない項目は拒否される(ledger):
    p = ledger.add("田中邸 新築")
    with pytest.raises(CompanyError, match="更新できない項目"):
        ledger.update(p["id"], "改ざん", id="0000")


def test_存在しない案件の取得と更新はエラー(ledger):
    with pytest.raises(CompanyError, match="見つかりません"):
        ledger.get("deadbeef")
    with pytest.raises(CompanyError, match="見つかりません"):
        ledger.update("deadbeef", "何か")


def test_一覧は既定で進行中のみ返す(ledger):
    a = ledger.add("A邸")
    ledger.add("B邸")
    ledger.update(a["id"], "他社に決定", status="lost")
    assert [p["name"] for p in ledger.list()] == ["B邸"]
    assert len(ledger.list(status="all")) == 2
    assert [p["name"] for p in ledger.list(status="lost")] == ["A邸"]


def test_一覧は期限の近い順に並ぶ(ledger):
    late = ledger.add("後回し")
    soon = ledger.add("急ぎ")
    ledger.update(late["id"], "期限設定", next_due="2026-12-01")
    ledger.update(soon["id"], "期限設定", next_due="2026-09-01")
    assert [p["name"] for p in ledger.list()] == ["急ぎ", "後回し"]


def test_期限未設定の案件は末尾に回る(ledger):
    ledger.add("未設定")
    dated = ledger.add("期限あり")
    ledger.update(dated["id"], "期限設定", next_due="2026-09-01")
    assert [p["name"] for p in ledger.list()] == ["期限あり", "未設定"]


def test_担当者と語句で絞り込める(ledger):
    ledger.add("田中邸 新築", "田中様", owner="eigyo", site="世田谷区")
    ledger.add("佐藤ビル 改修", "佐藤様", owner="bim", site="港区")
    assert [p["name"] for p in ledger.list(owner="bim")] == ["佐藤ビル 改修"]
    assert [p["name"] for p in ledger.list(query="世田谷")] == ["田中邸 新築"]
    assert [p["name"] for p in ledger.list(query="佐藤")] == ["佐藤ビル 改修"]


def test_log_は項目を変えず履歴だけ足す(ledger):
    p = ledger.add("田中邸 新築", by="eigyo")
    before = ledger.get(p["id"])["stage"]
    updated = ledger.log(p["id"], "施主から間取り変更の相談", by="bim")
    assert updated["stage"] == before
    assert updated["history"][-1]["entry"] == "施主から間取り変更の相談"
    assert updated["history"][-1]["by"] == "bim"


def test_空の履歴追記は拒否される(ledger):
    p = ledger.add("田中邸 新築")
    with pytest.raises(CompanyError, match="履歴の内容は必須"):
        ledger.log(p["id"], "   ")


def test_パイプラインはステージ別の進行中件数を返す(ledger):
    a = ledger.add("A邸")
    ledger.add("B邸")
    lost = ledger.add("C邸")
    ledger.update(a["id"], "相談実施", stage="初回相談")
    ledger.update(lost["id"], "失注", status="lost")
    counts = ledger.pipeline()
    assert counts["反響"] == 1  # B邸のみ。失注した C邸は数えない
    assert counts["初回相談"] == 1
    assert sum(counts.values()) == 2


def test_台帳は事務所で共有される(tmp_path):
    """別インスタンスから読んでも同じ台帳を指す。"""
    ProjectLedger(tmp_path).add("田中邸 新築", by="shukyaku")
    assert [p["name"] for p in ProjectLedger(tmp_path).list()] == ["田中邸 新築"]
