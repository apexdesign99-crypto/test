"""マーケティング担当の要——掲載許諾と表現チェックのテスト。

事故になるのは「許諾のない案件を公開する」ことなので、
未確認・不可のときに確実に止まることを重点的に確認する。
"""

import pytest

from ai_employee.company import CompanyError, ProjectLedger
from ai_employee.copycheck import review_copy


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


@pytest.fixture
def project(ledger) -> dict:
    return ledger.add("田中邸 新築", "田中様", "戸建住宅", by="shukyaku")


# ---------------------------------------------------------------- 掲載許諾


def test_新しい案件の許諾は未確認から始まる(ledger, project):
    """既定を「許諾済」にしない。確認しないと公開できない状態から始める。"""
    status = ledger.publication_status(project["id"])
    assert status["consent_status"] == "未確認"
    assert status["publishable"] is False
    assert "原稿を書いてはいけない" in status["guidance"]


def test_許諾済なら発信できる(ledger, project):
    ledger.record_consent(project["id"], "許諾済", by="marke")
    assert ledger.publication_status(project["id"])["publishable"] is True


def test_条件付きは条件がそのまま指示になる(ledger, project):
    ledger.record_consent(project["id"], "条件付き", "施主名は伏せる。外観写真のみ。", by="marke")
    status = ledger.publication_status(project["id"])
    assert status["publishable"] is True
    assert "施主名は伏せる" in status["guidance"]
    assert status["conditions"] == "施主名は伏せる。外観写真のみ。"


def test_条件付きで条件が空なら拒否される(ledger, project):
    """条件を書かずに条件付きにすると、何が許されているか分からなくなる。"""
    with pytest.raises(CompanyError, match="条件の記載が必須"):
        ledger.record_consent(project["id"], "条件付き", "  ", by="marke")


def test_不可なら匿名化しても書くなと指示される(ledger, project):
    ledger.record_consent(project["id"], "不可", by="marke")
    status = ledger.publication_status(project["id"])
    assert status["publishable"] is False
    assert "匿名化しても書かない" in status["guidance"]


def test_不正な許諾状態は拒否される(ledger, project):
    with pytest.raises(CompanyError, match="不正な許諾状態"):
        ledger.record_consent(project["id"], "たぶん大丈夫", by="marke")


def test_許諾の変更は履歴に残る(ledger, project):
    ledger.record_consent(project["id"], "許諾済", by="marke")
    last = ledger.get(project["id"])["history"][-1]
    assert last["by"] == "marke"
    assert "未確認 → 許諾済" in last["entry"]


# ---------------------------------------------------------------- 発信記録


def test_許諾のない案件は発信を記録できない(ledger, project):
    """ここが要。許諾を取らずに出した、が記録上も成立しない。"""
    with pytest.raises(CompanyError, match="掲載許諾が「未確認」"):
        ledger.log_publication(project["id"], "Instagram", "新しい住まい", by="marke")


def test_不可の案件も発信を記録できない(ledger, project):
    ledger.record_consent(project["id"], "不可", by="marke")
    with pytest.raises(CompanyError, match="掲載許諾が「不可」"):
        ledger.log_publication(project["id"], "Instagram", "新しい住まい", by="marke")


def test_発信を記録すると履歴とチャネルに残る(ledger, project):
    ledger.record_consent(project["id"], "許諾済", by="marke")
    record = ledger.log_publication(
        project["id"], "HP施工事例", "光を通す木造の家", url="https://example.com/works/1", by="marke"
    )
    assert record["channel"] == "HP施工事例"
    status = ledger.publication_status(project["id"])
    assert status["publications"][0]["title"] == "光を通す木造の家"
    assert "HP施工事例 で発信" in ledger.get(project["id"])["history"][-1]["entry"]


def test_不正なチャネルとタイトル欠落は拒否される(ledger, project):
    ledger.record_consent(project["id"], "許諾済", by="marke")
    with pytest.raises(CompanyError, match="不正なチャネル"):
        ledger.log_publication(project["id"], "テレビCM", "タイトル")
    with pytest.raises(CompanyError, match="タイトルは必須"):
        ledger.log_publication(project["id"], "Instagram", "   ")


# ------------------------------------------------------------ ネタの棚卸し


def test_許諾済と要許諾が分けて返る(ledger):
    ok = ledger.add("許諾済の案件")
    pending = ledger.add("未確認の案件")
    ledger.add("不可の案件")
    ledger.record_consent(ok["id"], "許諾済")
    ledger.record_consent(ledger.list(query="不可の案件")[0]["id"], "不可")

    result = ledger.publication_candidates()
    assert [p["name"] for p in result["ready"]] == ["許諾済の案件"]
    assert [p["name"] for p in result["needs_consent"]] == [pending["name"]]


def test_不可の案件は候補にすら出ない(ledger, project):
    ledger.record_consent(project["id"], "不可")
    result = ledger.publication_candidates()
    assert result["ready"] == []
    assert result["needs_consent"] == []


def test_発信済みのチャネルは候補から外れる(ledger, project):
    ledger.record_consent(project["id"], "許諾済")
    ledger.log_publication(project["id"], "Instagram", "投稿")
    assert ledger.publication_candidates(channel="Instagram")["ready"] == []
    # 別チャネルではまだ出せる
    assert len(ledger.publication_candidates(channel="HP施工事例")["ready"]) == 1


def test_用途種別で絞れる(ledger):
    house = ledger.add("戸建の案件", kind="戸建住宅")
    ledger.add("店舗の案件", kind="店舗")
    ledger.record_consent(house["id"], "許諾済")
    result = ledger.publication_candidates(kind="戸建住宅")
    assert [p["name"] for p in result["ready"]] == ["戸建の案件"]


def test_不正なチャネルや用途は拒否される(ledger):
    with pytest.raises(CompanyError, match="不正なチャネル"):
        ledger.publication_candidates(channel="テレビCM")
    with pytest.raises(CompanyError, match="不正な用途種別"):
        ledger.publication_candidates(kind="宇宙船")


# ---------------------------------------------------------------- 表現チェック


@pytest.mark.parametrize(
    "text,category",
    [
        ("地域No.1の設計事務所です", "最上級表現"),
        ("業界最高の品質", "最上級表現"),
        ("当社だけの工法です", "唯一性の主張"),
        ("必ずご満足いただけます", "断定・保証"),
        ("どんな要望も叶えます", "完全性の主張"),
        ("他社より安くご提供", "比較優位の主張"),
        ("田中様邸が完成", "個人が特定される情報"),
        ("世田谷区2丁目5-3", "個人が特定される情報"),
        ("施工実績200件", "裏付けが要る数値"),
        ("創業25年", "裏付けが要る数値"),
        ("満足度98%", "裏付けが要る数値"),
    ],
)
def test_事故になりやすい表現を拾う(text, category):
    result = review_copy(text)
    assert result["count"] >= 1
    assert category in result["by_category"]


@pytest.mark.parametrize(
    "text",
    [
        "S様邸は世田谷区に完成しました。",           # 匿名化済み
        "2027年春の竣工を予定しています。",           # 日付は実績数値ではない
        "1978年設立の建物を改修しました。",           # 築年の記述
        "木の質感を生かした住まいを設計しました。",
    ],
)
def test_問題のない表現は拾わない(text):
    assert review_copy(text)["count"] == 0


def test_行番号と前後の文脈を返す():
    result = review_copy("一行目です。\n二行目に必ず問題があります。")
    flag = result["flags"][0]
    assert flag["line"] == 2
    assert "〈必ず〉" in flag["context"]
    assert flag["reason"]


def test_重なる指摘は一度だけ数える():
    """同じ箇所に複数ルールが当たっても二重に指摘しない。"""
    result = review_copy("日本一の実績")
    phrases = [f["phrase"] for f in result["flags"]]
    assert len(phrases) == len(set(phrases))


def test_指摘は行順に並ぶ():
    text = "三行目に必ず。\n\n一行目にNo.1。"
    lines = [f["line"] for f in review_copy(text)["flags"]]
    assert lines == sorted(lines)


def test_適法性の判断ではないと明記される():
    """通ったことを「問題なし」と報告させないため。"""
    assert "適法性の判断ではない" in review_copy("普通の文章です")["disclaimer"]


def test_空の原稿は拒否される():
    with pytest.raises(ValueError, match="原稿が空"):
        review_copy("   \n  ")
