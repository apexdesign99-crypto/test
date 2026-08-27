"""競合台帳のテスト。

他社について事実でないことを社内に残さないため、
出典なしで登録できないことを重点的に確認する。
"""

import pytest

from ai_employee.competitor import APPEAL_AXES, CompetitorError, CompetitorLedger


@pytest.fixture
def ledger(tmp_path) -> CompetitorLedger:
    return CompetitorLedger(tmp_path)


def seed(ledger: CompetitorLedger) -> None:
    ledger.record("A工務店", "愛知県一宮市", ["https://example.com/a"], "工務店",
                  ["高性能(断熱・気密)", "価格の安さ"], by="shukyaku")
    ledger.record("Bハウス", "愛知県名古屋市", ["https://example.com/b"], "ハウスメーカー",
                  ["耐震・構造", "価格の安さ"], by="shukyaku")
    ledger.record("C設計室", "岐阜県岐阜市", ["https://example.com/c"], "設計事務所",
                  ["デザイン性"], by="shukyaku")


# ---------------------------------------------------------------- 出典


@pytest.mark.parametrize("sources", [[], [""], ["   "], None])
def test_出典がなければ登録できない(ledger, sources):
    """ここが要。記憶で他社の情報を書かせない。"""
    with pytest.raises(CompetitorError, match="出典"):
        ledger.record("A工務店", "愛知県一宮市", sources)


def test_出典なしのエラーは推測禁止を明言する(ledger):
    with pytest.raises(CompetitorError) as exc:
        ledger.record("A工務店", "愛知県一宮市", [])
    assert "推測で競合の情報を登録してはいけません" in str(exc.value)


def test_出典は前後の空白を落として保存される(ledger):
    record = ledger.record("A工務店", "愛知県一宮市", ["  https://example.com/a  ", ""])
    assert record["sources"] == ["https://example.com/a"]


# ---------------------------------------------------------------- 登録


def test_必須項目が欠ければ拒否される(ledger):
    with pytest.raises(CompetitorError, match="競合名は必須"):
        ledger.record("  ", "愛知県一宮市", ["https://example.com/a"])
    with pytest.raises(CompetitorError, match="対象エリアは必須"):
        ledger.record("A工務店", "", ["https://example.com/a"])


def test_不正な業態と訴求軸は拒否される(ledger):
    with pytest.raises(CompetitorError, match="不正な業態"):
        ledger.record("A", "愛知", ["https://example.com/a"], "宇宙開発")
    with pytest.raises(CompetitorError, match="不正な訴求軸"):
        ledger.record("A", "愛知", ["https://example.com/a"], "工務店", ["雰囲気の良さ"])


def test_フォロワー数が負なら拒否される(ledger):
    with pytest.raises(CompetitorError, match="0 以上"):
        ledger.record("A", "愛知", ["https://example.com/a"], followers=-1)


def test_同じ会社と商圏なら上書きされる(ledger):
    first = ledger.record("A工務店", "愛知県一宮市", ["https://example.com/a"],
                          followers=3000, by="shukyaku")
    second = ledger.record("A工務店", "愛知県一宮市", ["https://example.com/a2"],
                           followers=3500, by="shukyaku")
    assert first["id"] == second["id"]
    assert len(ledger.list()) == 1
    assert ledger.get(first["id"])["followers"] == 3500


def test_調査者と調査日が残る(ledger):
    record = ledger.record("A工務店", "愛知県一宮市", ["https://example.com/a"], by="shukyaku")
    assert record["researched_by"] == "shukyaku"
    assert record["researched_at"]


def test_削除できる(ledger):
    record = ledger.record("A工務店", "愛知県一宮市", ["https://example.com/a"])
    ledger.delete(record["id"])
    assert ledger.list() == []
    with pytest.raises(CompetitorError, match="見つかりません"):
        ledger.delete(record["id"])


# ---------------------------------------------------------------- 検索


def test_商圏と業態で絞れる(ledger):
    seed(ledger)
    assert len(ledger.list(area="愛知")) == 2
    assert [r["name"] for r in ledger.list(area="岐阜")] == ["C設計室"]
    assert [r["name"] for r in ledger.list(company_type="設計事務所")] == ["C設計室"]


def test_新しく調べた順に返る(ledger):
    seed(ledger)
    stamps = [r["researched_at"] for r in ledger.list()]
    assert stamps == sorted(stamps, reverse=True)


def test_不正な業態での検索は拒否される(ledger):
    with pytest.raises(CompetitorError, match="不正な業態"):
        ledger.list(company_type="宇宙開発")


# ---------------------------------------------------------------- 集計


def test_訴求軸を集計して混雑度を出す(ledger):
    seed(ledger)
    report = ledger.appeal_report()
    assert report["competitor_count"] == 3
    assert report["crowded_axes"][0] == ("価格の安さ", 2)
    assert "自然素材" in report["empty_axes"]


def test_自社の軸と突き合わせて差別化候補を出す(ledger):
    seed(ledger)
    report = ledger.appeal_report(own_axes=["デザイン性", "自然素材", "施主との距離・伴走"])
    assert report["differentiators"] == ["自然素材", "施主との距離・伴走"]
    assert report["contested_axes"] == [("デザイン性", 1)]


def test_未知の自社軸は無視される(ledger):
    seed(ledger)
    report = ledger.appeal_report(own_axes=["デザイン性", "謎の強み"])
    assert report["own_axes"] == ["デザイン性"]


def test_商圏で絞って集計できる(ledger):
    seed(ledger)
    assert ledger.appeal_report(area="岐阜")["competitor_count"] == 1


def test_集計には断定を戒める注記が付く(ledger):
    """空いている軸 = 狙い目、と読ませないため。"""
    caveat = ledger.appeal_report()["caveat"]
    assert "市場全体ではない" in caveat
    assert "需要がないから空いている可能性" in caveat


def test_競合ゼロでも集計は落ちない(ledger):
    report = ledger.appeal_report()
    assert report["competitor_count"] == 0
    assert report["crowded_axes"] == []
    assert len(report["empty_axes"]) == len(APPEAL_AXES) - 1  # 「その他」を除く
