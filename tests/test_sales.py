"""営業担当の要——ヒアリングの欠落検出と、概算算定のテスト。

どちらも「AI に数字や事実を作らせない」ための仕組みなので、
未確認・未設定のときに黙って埋まらないことを重点的に確認する。
"""

import pytest

from ai_employee.company import (
    HEARING_REQUIRED,
    CompanyError,
    OfficeProfile,
    ProjectLedger,
)


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


@pytest.fixture
def project(ledger) -> dict:
    return ledger.add("佐々木様 新築", "佐々木様", "戸建住宅", by="shukyaku")


@pytest.fixture
def office() -> OfficeProfile:
    return OfficeProfile(
        name="A設計",
        unit_prices={"戸建住宅": [80, 100]},
        design_fee_rate=10,
        design_fee_minimum=300,
    )


# ------------------------------------------------------------ ヒアリング


def test_新しい案件は全項目が未確認(ledger, project):
    gaps = ledger.hearing_gaps(project["id"])
    assert gaps["recorded"] == {}
    assert gaps["ready_for_proposal"] is False
    assert len(gaps["missing_required"]) == len(HEARING_REQUIRED)


def test_聞けた項目だけが記録される(ledger, project):
    ledger.record_hearing(project["id"], {"budget": "総額4500万円"}, by="eigyo")
    gaps = ledger.hearing_gaps(project["id"])
    assert gaps["recorded"] == {"予算(総額)": "総額4500万円"}
    assert "予算(総額)" not in [m["label"] for m in gaps["missing"]]


def test_ヒアリングは部分更新で積み上がる(ledger, project):
    ledger.record_hearing(project["id"], {"budget": "4500万円"}, by="eigyo")
    ledger.record_hearing(project["id"], {"land": "所有"}, by="eigyo")
    assert set(ledger.get(project["id"])["requirements"]) == {"budget", "land"}


def test_必須が揃うまで提案可にならない(ledger, project):
    ledger.record_hearing(
        project["id"], {key: "確認済み" for key in HEARING_REQUIRED}, by="eigyo"
    )
    gaps = ledger.hearing_gaps(project["id"])
    assert gaps["ready_for_proposal"] is True
    assert gaps["missing_required"] == []
    # 任意項目は残っていてよい
    assert gaps["missing"]


def test_空文字は記録されず未確認のまま残る(ledger, project):
    """「聞いたことにする」を防ぐ。"""
    with pytest.raises(CompanyError, match="記録する内容がありません"):
        ledger.record_hearing(project["id"], {"budget": "  "}, by="eigyo")
    assert ledger.hearing_gaps(project["id"])["recorded"] == {}


def test_未知の項目は拒否される(ledger, project):
    with pytest.raises(CompanyError, match="未知です"):
        ledger.record_hearing(project["id"], {"favorite_color": "青"}, by="eigyo")


def test_ヒアリングは履歴に残る(ledger, project):
    ledger.record_hearing(project["id"], {"budget": "4500万円"}, by="eigyo")
    last = ledger.get(project["id"])["history"][-1]
    assert last["by"] == "eigyo"
    assert "ヒアリングを記録" in last["entry"]
    assert "予算(総額)" in last["entry"]


def test_ヒアリング記録は最終更新を進める(ledger, project):
    """記録した = 接触したなので、追客対象から外れる。"""
    before = ledger.get(project["id"])["updated_at"]
    ledger.record_hearing(project["id"], {"budget": "4500万円"}, by="eigyo")
    assert ledger.get(project["id"])["updated_at"] >= before
    assert ledger.stale(0) != []  # days=0 は全件が対象


def test_存在しない案件へのヒアリング記録はエラー(ledger):
    with pytest.raises(CompanyError, match="見つかりません"):
        ledger.record_hearing("deadbeef", {"budget": "4500万円"})


# ---------------------------------------------------------------- 概算算定


def test_坪から工事費と設計料を算定する(office):
    r = office.estimate("戸建住宅", floor_area_tsubo=35)
    assert r["construction_cost"] == {"low": 2800, "high": 3500}
    assert r["design_fee"]["high"] == 350
    assert "35" in r["basis"] and "2,800" in r["basis"]


def test_平米でも算定できる(office):
    r = office.estimate("戸建住宅", floor_area_sqm=115.7)
    assert r["floor_area_tsubo"] == 35.0


def test_最低額が下回った分に適用される(office):
    r = office.estimate("戸建住宅", floor_area_tsubo=18)
    assert r["design_fee"]["low"] == 300
    assert r["design_fee"]["applied_minimum"] is True
    assert "最低額" in r["basis"]


def test_最低額を上回れば適用されない(office):
    r = office.estimate("戸建住宅", floor_area_tsubo=50)
    assert r["design_fee"]["applied_minimum"] is False


def test_最低額未設定でも算定できる():
    office = OfficeProfile(name="A設計", unit_prices={"店舗": [60, 90]}, design_fee_rate=8)
    r = office.estimate("店舗", floor_area_tsubo=20)
    assert r["design_fee"] == {
        "low": 96,
        "high": 144,
        "rate_percent": 8,
        "minimum": None,
        "applied_minimum": False,
    }


def test_坪単価未設定の用途では算定を拒否する(office):
    """ここが要。黙って推測した数字を出させない。"""
    with pytest.raises(CompanyError, match="坪単価が事務所プロフィールに未設定"):
        office.estimate("共同住宅", floor_area_tsubo=100)


def test_料率未設定では算定を拒否する():
    office = OfficeProfile(name="A設計", unit_prices={"戸建住宅": [80, 100]})
    with pytest.raises(CompanyError, match="設計監理料率.*未設定"):
        office.estimate("戸建住宅", floor_area_tsubo=35)


def test_面積の指定は坪か平米のどちらか一方(office):
    with pytest.raises(CompanyError, match="どちらか一方"):
        office.estimate("戸建住宅")
    with pytest.raises(CompanyError, match="どちらか一方"):
        office.estimate("戸建住宅", floor_area_tsubo=35, floor_area_sqm=115)


@pytest.mark.parametrize("area", [0, -10])
def test_面積が0以下なら拒否される(office, area):
    with pytest.raises(CompanyError, match="0 より大きい"):
        office.estimate("戸建住宅", floor_area_tsubo=area)


def test_不正な用途種別は拒否される(office):
    with pytest.raises(CompanyError, match="不正な用途種別"):
        office.estimate("宇宙ステーション", floor_area_tsubo=35)


def test_概算には必ず但し書きが付く(office):
    r = office.estimate("戸建住宅", floor_area_tsubo=35)
    assert "確定金額ではない" in r["caveat"]
    assert "地盤" in r["caveat"]


def test_算定可否を事前に判定できる(office):
    assert office.can_estimate("戸建住宅") is True
    assert office.can_estimate("共同住宅") is False
    assert OfficeProfile(name="A設計").can_estimate("戸建住宅") is False


# --------------------------------------------------- プロンプトへの反映


def test_単価設定済みなら自分で計算するなと指示される(office):
    prompt = office.as_prompt()
    assert "戸建住宅 80〜100 万円/坪" in prompt
    assert "自分で掛け算をしない" in prompt


def test_単価未設定なら概算を出すなと指示される():
    prompt = OfficeProfile(name="A設計").as_prompt()
    assert "概算金額を出せない" in prompt
    assert "推測した数字を書いてはいけない" in prompt
