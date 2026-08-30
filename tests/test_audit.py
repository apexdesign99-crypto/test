"""社内点検のテスト。

「見つけるべきものを見つける」「安全だと言わない」を重点的に確認する。
"""

import json
import stat
from datetime import timedelta

import pytest

from ai_employee.audit import CHECKS, SEVERITIES, audit
from ai_employee.company import OfficeProfile, ProjectLedger
from ai_employee.competitor import CompetitorLedger
from ai_employee.instagram_api import Credentials, credentials_path, save_credentials
from ai_employee.instagram_plan import InstagramPlan
from ai_employee.profile import build_profile
from ai_employee.workspace import Workspace, now


@pytest.fixture
def office(tmp_path):
    OfficeProfile(name="A設計").save(tmp_path)
    return tmp_path


def findings(root, category=None, severity=None):
    result = audit(root)
    hits = result["findings"]
    if category:
        hits = [f for f in hits if f["category"] == category]
    if severity:
        hits = [f for f in hits if f["severity"] == severity]
    return hits


# ---------------------------------------------------------------- 資格情報


def test_トークンが他のファイルに漏れていたら重大として挙げる(office):
    """ここが要。業務メモに貼られたトークンは実害が大きい。"""
    save_credentials(Credentials(access_token="IGAAsecretvalue123"), office)
    workspace = Workspace("marke", office)
    workspace.save_profile(build_profile("marke", "マーケ AI", "marketing"))
    workspace.add_note("引き継ぎ", "トークンは IGAAsecretvalue123 です")

    hits = findings(office, "資格情報", "高")
    assert len(hits) == 1
    assert "notes.jsonl" in hits[0]["where"]
    assert "失効させて取り直して" in hits[0]["action"]


def test_短いトークンは誤検出を避けるため走査しない(office):
    save_credentials(Credentials(access_token="abc"), office)
    Workspace("marke", office).ensure()
    assert findings(office, "資格情報", "高") == []


def test_資格情報のパーミッションが緩ければ挙げる(office):
    save_credentials(Credentials(access_token="IGAAsecretvalue123"), office)
    path = credentials_path(office)
    path.chmod(0o644)
    hits = findings(office, "資格情報", "高")
    assert any("他者から読める" in h["detail"] for h in hits)


def test_期限切れと期限間近を挙げる(office):
    save_credentials(Credentials(
        access_token="IGAAsecretvalue123",
        expires_at=(now() - timedelta(days=1)).isoformat(timespec="seconds"),
    ), office)
    assert any("有効期限が切れて" in h["detail"] for h in findings(office, "資格情報"))

    save_credentials(Credentials(
        access_token="IGAAsecretvalue123",
        expires_at=(now() + timedelta(days=5, hours=1)).isoformat(timespec="seconds"),
    ), office)
    assert any("残りが 5 日" in h["detail"] for h in findings(office, "資格情報"))


def test_未接続なら資格情報の指摘は出ない(office, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    assert findings(office, "資格情報") == []


# ---------------------------------------------------------------- 個人情報


def test_競合台帳の個人情報を挙げる(office):
    CompetitorLedger(office).record(
        "あかつき工務店", "愛知県一宮市", ["https://example.com/a"], "工務店",
        note="山本様邸のコンペで競合。連絡先 090-1234-5678")
    hits = findings(office, "個人情報")
    assert len(hits) == 1
    assert "山本様邸" in hits[0]["detail"]
    assert "090-1234-5678" in hits[0]["detail"]


def test_投稿計画の実名を挙げる(office):
    InstagramPlan(office).add("2026-09-10", "works", "田中様邸の中庭")
    hits = findings(office, "個人情報")
    assert any("田中様邸" in h["detail"] for h in hits)
    assert any("S様邸" in h["action"] for h in hits)


def test_成果物ファイルの個人情報を挙げる(office):
    workspace = Workspace("jimu", office)
    workspace.save_profile(build_profile("jimu", "事務 AI", "office"))
    workspace.write_file("請求/2026-09.md", "佐藤様邸\n世田谷区2丁目5-3\nsato@example.com")
    hits = findings(office, "個人情報")
    assert len(hits) == 1
    for expected in ("佐藤様邸", "2丁目5-3", "sato@example.com"):
        assert expected in hits[0]["detail"]


def test_案件台帳の個人情報は指摘しない(office):
    """案件台帳と業務メモは本来の置き場所なので見ない。"""
    ledger = ProjectLedger(office)
    ledger.add("佐藤様邸 新築", "佐藤様", "戸建住宅", site="世田谷区2丁目5-3")
    assert findings(office, "個人情報") == []


def test_匿名化済みの表記は指摘しない(office):
    InstagramPlan(office).add("2026-09-10", "works", "S様邸の中庭")
    assert findings(office, "個人情報") == []


# ---------------------------------------------------------------- 掲載許諾


def test_許諾なしの発信記録を重大として挙げる(office):
    ledger = ProjectLedger(office)
    project = ledger.add("K様邸 新築", "K様", "戸建住宅")
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    data[0]["publications"] = [
        {"at": "2026-08-01T10:00:00", "channel": "Instagram",
         "title": "完成しました", "url": "", "by": "marke"}]
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    hits = findings(office, "掲載許諾", "高")
    assert len(hits) == 1
    assert "未確認" in hits[0]["detail"]


def test_条件付きなのに条件が空なら挙げる(office):
    ledger = ProjectLedger(office)
    project = ledger.add("T様邸")
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    data[0]["consent"] = {"status": "条件付き", "conditions": "",
                          "at": "2026-08-01T00:00:00", "by": "marke"}
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert any("条件が記録されていません" in h["detail"]
               for h in findings(office, "掲載許諾"))


def test_正しく許諾を取った発信は指摘しない(office):
    ledger = ProjectLedger(office)
    project = ledger.add("K様邸 新築")
    ledger.record_consent(project["id"], "許諾済")
    ledger.log_publication(project["id"], "Instagram", "完成しました")
    assert findings(office, "掲載許諾") == []


def test_不正な許諾状態を挙げる(office):
    ledger = ProjectLedger(office)
    ledger.add("T様邸")
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    data[0]["consent"] = {"status": "たぶん大丈夫", "conditions": ""}
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert any("状態が不正" in h["detail"] for h in findings(office, "掲載許諾"))


# ---------------------------------------------------------------- 権限


def test_Web検索の権限を持つ社員を挙げる(office):
    for eid, tpl in [("shukyaku", "lead"), ("jimu", "office")]:
        Workspace(eid, office).save_profile(build_profile(eid, eid, tpl))
    hits = findings(office, "権限")
    assert [h["where"].split("(")[0] for h in hits] == ["shukyaku"]
    assert "指示文が紛れうる" in hits[0]["action"]


def test_既定にないツールを持つ社員を挙げる(office):
    profile = build_profile("jimu", "事務 AI", "office")
    profile.tools = profile.tools + ["run_audit"] if "run_audit" not in profile.tools \
        else profile.tools
    profile.tools = [*profile.tools, "secret_tool"]
    Workspace("jimu", office).save_profile(profile)
    assert any("既定にないツール" in h["detail"] for h in findings(office, "権限"))


# ---------------------------------------------------------------- 台帳


def test_壊れた台帳を重大として挙げる(office):
    (office / "_company" / "projects.json").write_text("{壊れています", encoding="utf-8")
    hits = findings(office, "台帳", "高")
    assert len(hits) == 1
    assert "読み込めません" in hits[0]["detail"]


def test_台帳が壊れていても点検全体は止まらない(office):
    """壊れているときこそ点検が要る。1項目の失敗で他が見られなくなると困る。"""
    (office / "_company" / "projects.json").write_text("{壊れています", encoding="utf-8")
    save_credentials(Credentials(access_token="IGAAsecretvalue123"), office)
    credentials_path(office).chmod(0o644)

    result = audit(office)
    categories = {f["category"] for f in result["findings"]}
    assert "台帳" in categories        # 壊れていることを挙げ
    assert "資格情報" in categories    # 他の点検も続いている
    # 点検できなかった項目があることも明示する
    assert any("点検できませんでした" in f["detail"] for f in result["findings"])


# ---------------------------------------------------------------- 全体


def test_安全だとは言わない(office):
    """指摘ゼロでも「問題なし」とは書かない。"""
    result = audit(office)
    assert result["count"] == 0
    assert "安全性の保証ではない" in result["disclaimer"]
    assert "指摘がゼロでも問題がないとは限らない" in result["disclaimer"]


def test_何を点検したかを返す(office):
    """点検範囲を示さないと、見ていない領域を見たと誤解される。"""
    assert len(audit(office)["checked"]) >= 5


def test_重大度の高い順に並ぶ(office):
    ledger = ProjectLedger(office)
    ledger.add("K様邸")
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    data[0]["publications"] = [{"at": "2026-08-01T10:00:00", "channel": "Instagram",
                               "title": "x", "url": "", "by": "marke"}]
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    Workspace("shukyaku", office).save_profile(build_profile("shukyaku", "集客", "lead"))

    order = {level: index for index, level in enumerate(SEVERITIES)}
    levels = [order[f["severity"]] for f in audit(office)["findings"]]
    assert levels == sorted(levels)


def test_項目を指定して点検できる(office):
    save_credentials(Credentials(access_token="IGAAsecretvalue123"), office)
    Workspace("shukyaku", office).save_profile(build_profile("shukyaku", "集客", "lead"))
    result = audit(office, only="permissions")
    assert all(f["category"] == "権限" for f in result["findings"])
    assert len(result["checked"]) == 1


def test_不正な項目は拒否される(office):
    with pytest.raises(ValueError, match="不正な点検項目"):
        audit(office, only="なんとなく")


def test_空の事務所でも落ちない(tmp_path):
    result = audit(tmp_path)
    assert result["count"] == 0


def test_全ての点検項目が実行できる(office):
    for key in CHECKS:
        audit(office, only=key)
