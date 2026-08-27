"""事務所プロフィールと、集客が使う集計のテスト。"""

import json
from datetime import timedelta

import pytest

from ai_employee.company import CompanyError, OfficeProfile, ProjectLedger
from ai_employee.workspace import now


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


def age(ledger: ProjectLedger, name: str, days: int) -> None:
    """指定案件の最終更新を過去にずらす(放置状態の再現)。"""
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    for project in data:
        if project["name"] == name:
            project["updated_at"] = (now() - timedelta(days=days)).isoformat(
                timespec="seconds"
            )
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------ 事務所プロフィール


def test_未設定なら事務所固有の情報を書くなと指示される(tmp_path):
    """ここが本丸。空のまま書かせると社員が料金やエリアを作る。"""
    prompt = OfficeProfile.load(tmp_path).as_prompt()
    assert "未設定" in prompt
    assert "書いてはいけません" in prompt
    assert "要記入" in prompt


def test_未設定判定は事務所名で決まる(tmp_path):
    assert OfficeProfile().is_configured() is False
    assert OfficeProfile(name="  ").is_configured() is False
    assert OfficeProfile(name="A設計").is_configured() is True


def test_設定済みなら内容がプロンプトに載る(tmp_path):
    office = OfficeProfile(
        name="アペックス設計事務所",
        areas=["東京23区", "川崎市"],
        fee_policy="設計監理料は工事費の10%",
        consultation_flow=["お問い合わせ", "初回相談"],
    )
    prompt = office.as_prompt()
    assert "アペックス設計事務所" in prompt
    assert "東京23区、川崎市" in prompt
    assert "1. お問い合わせ" in prompt
    # 設定済みでも「書かれていないことは書くな」は残す
    assert "【要確認】" in prompt


def test_空の項目はプロンプトに出さない():
    prompt = OfficeProfile(name="A設計").as_prompt()
    assert "所在地" not in prompt
    assert "初回相談の流れ" not in prompt


def test_保存と読み込みで内容が一致する(tmp_path):
    office = OfficeProfile(name="A設計", areas=["東京"], contact="03-0000-0000")
    path = office.save(tmp_path)
    assert path == tmp_path / "_company" / "office.json"
    assert OfficeProfile.load(tmp_path) == office


def test_未知の項目を含む事務所プロフィールは拒否される(tmp_path):
    (tmp_path / "_company").mkdir(parents=True)
    (tmp_path / "_company" / "office.json").write_text(
        json.dumps({"name": "A設計", "employees": 12}), encoding="utf-8"
    )
    with pytest.raises(CompanyError, match="未知の項目"):
        OfficeProfile.load(tmp_path)


# ---------------------------------------------------------- 追客漏れの検知


def test_動いていない案件だけを放置が長い順に返す(ledger):
    ledger.add("放置30日")
    ledger.add("放置18日")
    ledger.add("直近に更新")
    age(ledger, "放置30日", 30)
    age(ledger, "放置18日", 18)

    assert [p["name"] for p in ledger.stale(14)] == ["放置30日", "放置18日"]
    assert [p["name"] for p in ledger.stale(20)] == ["放置30日"]
    assert ledger.stale(60) == []


def test_決着した案件は追客対象に入らない(ledger):
    won = ledger.add("受注済")
    ledger.update(won["id"], "契約", status="won")
    age(ledger, "受注済", 90)
    assert ledger.stale(14) == []


def test_台帳を更新すれば追客対象から外れる(ledger):
    """更新日を最終接触日として扱うという設計の確認。"""
    project = ledger.add("放置案件")
    age(ledger, "放置案件", 30)
    assert len(ledger.stale(14)) == 1
    ledger.update(project["id"], "電話でフォロー", next_action="再提案")
    assert ledger.stale(14) == []


def test_ステージで絞り込める(ledger):
    a = ledger.add("反響のまま")
    b = ledger.add("提案中")
    ledger.update(b["id"], "提案", stage="プラン提案")
    age(ledger, "反響のまま", 30)
    age(ledger, "提案中", 30)
    assert [p["name"] for p in ledger.stale(14, stage="プラン提案")] == ["提案中"]
    assert a["id"] not in [p["id"] for p in ledger.stale(14, stage="プラン提案")]


def test_負の日数は拒否される(ledger):
    with pytest.raises(CompanyError, match="0 以上"):
        ledger.stale(-1)


# -------------------------------------------------------- 流入経路別の集計


def test_経路別に反響数と受注率を集計する(ledger):
    for name in ("A", "B", "C"):
        ledger.add(name, source="HP問い合わせ")
    ledger.add("D", source="紹介")
    won = ledger.list(query="A")[0]
    lost = ledger.list(query="B")[0]
    ledger.update(won["id"], "契約", status="won")
    ledger.update(lost["id"], "他社へ", status="lost")

    hp = next(r for r in ledger.by_source() if r["source"] == "HP問い合わせ")
    assert hp == {
        "source": "HP問い合わせ",
        "total": 3,
        "active": 1,
        "won": 1,
        "lost": 1,
        "other": 0,
        "win_rate": 50.0,
    }


def test_決着していない経路の受注率は_None(ledger):
    ledger.add("A", source="Instagram")
    row = next(r for r in ledger.by_source() if r["source"] == "Instagram")
    assert row["win_rate"] is None  # 0% と混同させない


def test_経路未記入は不明にまとめられる(ledger):
    ledger.add("A")
    ledger.add("B", source="   ")
    assert [r["source"] for r in ledger.by_source()] == ["不明"]
    assert ledger.by_source()[0]["total"] == 2


def test_反響の多い順に並ぶ(ledger):
    ledger.add("A", source="紹介")
    for name in ("B", "C"):
        ledger.add(name, source="HP問い合わせ")
    assert [r["source"] for r in ledger.by_source()] == ["HP問い合わせ", "紹介"]


def test_期間で絞り込める(ledger):
    ledger.add("A", source="HP問い合わせ")
    assert ledger.by_source(since="2000-01-01")[0]["total"] == 1
    assert ledger.by_source(since="2999-01-01") == []
