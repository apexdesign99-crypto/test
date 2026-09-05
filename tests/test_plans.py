"""過去プランデータベースのテスト。

事務所が Excel で蓄積した事例を読み、敷地条件から似た事例を探す。
「事例があること」を「成立すること」と混同させないことも確認する。
"""

import pytest

from ai_employee.plans import (
    KEY_COLUMN,
    PlanError,
    load_plans,
    search_similar,
    stats,
)

openpyxl = pytest.importorskip("openpyxl")

BASIC = ["物件ID", "建築年", "階数", "延床面積㎡", "延床面積坪",
         "敷地面積㎡", "敷地面積坪", "間口m", "前面道路方向", "敷地形状"]
FAMILY = ["物件ID", "家族人数", "LDK帖"]


def build(tmp_path, rows, family=None, sheet_name="①物件基本情報"):
    """テスト用の簡易データベースを作る。"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(BASIC)
    for row in rows:
        sheet.append(row)
    if family is not None:
        other = workbook.create_sheet("②家族・要望")
        other.append(FAMILY)
        for row in family:
            other.append(row)
    guide = workbook.create_sheet("使い方")
    guide.append(["説明", "ここは読み飛ばされる"])
    path = tmp_path / "db.xlsx"
    workbook.save(path)
    return path


ROWS = [
    ["P0001", 2023, "2階", 112.6, None, 165.3, None, 9.1, "南", "長方形"],
    ["P0002", 2024, "平屋", 92.3, None, 231.4, None, 14.5, "東", "長方形"],
    ["P0003", 2022, "2階", 105.2, None, 132.2, None, 7.2, "北", "旗竿"],
]


# ---------------------------------------------------------------- 読み込み


def test_物件IDで全シートを束ねる(tmp_path):
    path = build(tmp_path, ROWS, family=[["P0001", 4, 18], ["P0002", 2, 20]])
    plans = {p.plan_id: p for p in load_plans(path)}
    assert set(plans) == {"P0001", "P0002", "P0003"}
    assert plans["P0001"].get("家族人数") == 4        # 別シートの値が合流する
    assert plans["P0001"].get("前面道路方向") == "南"
    assert plans["P0003"].get("家族人数") is None      # 無い行は空のまま


def test_空行は読み飛ばす(tmp_path):
    """テンプレートは 500 行の空行を持つ。"""
    path = build(tmp_path, ROWS + [[None] * 10, ["", None, None, None, None,
                                                 None, None, None, None, None]])
    assert len(load_plans(path)) == 3


def test_使い方シートは読まない(tmp_path):
    path = build(tmp_path, ROWS)
    assert all(p.plan_id.startswith("P") for p in load_plans(path))


def test_物件ID列のないシートは無視する(tmp_path):
    path = build(tmp_path, ROWS)
    workbook = openpyxl.load_workbook(path)
    sheet = workbook.create_sheet("メモ")
    sheet.append(["自由記入"])
    sheet.append(["なにか"])
    workbook.save(path)
    assert len(load_plans(path)) == 3


def test_坪の数式が空でも平米から補う(tmp_path):
    """openpyxl で書いたファイルは数式のキャッシュ値を持たない。

    坪は類似検索の主軸なので、空のままにしない。
    """
    plans = {p.plan_id: p for p in load_plans(build(tmp_path, ROWS))}
    assert plans["P0001"].get("敷地面積坪") == pytest.approx(50.0, abs=0.1)
    assert plans["P0001"].get("延床面積坪") == pytest.approx(34.1, abs=0.1)


def test_ファイルがなければ置き場所を案内する(tmp_path):
    with pytest.raises(PlanError, match="design-data"):
        load_plans(tmp_path / "ない.xlsx")


def test_壊れたファイルは理由を示す(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes("これは xlsx ではありません".encode("utf-8"))
    with pytest.raises(PlanError, match="ファイルを読めません"):
        load_plans(path)


# ---------------------------------------------------------------- 類似検索


def test_条件に近い順に返る(tmp_path):
    path = build(tmp_path, ROWS, family=[["P0001", 4, 18], ["P0002", 2, 20],
                                         ["P0003", 4, 16]])
    result = search_similar(
        {"敷地面積坪": 50.0, "間口m": 9.0, "前面道路方向": "南", "家族人数": 4},
        limit=3, path=path)
    assert result["results"][0]["plan_id"] == "P0001"
    scores = [r["similarity"] for r in result["results"]]
    assert scores == sorted(scores, reverse=True)


def test_一致した項目を示す(tmp_path):
    """何が似ているのか分からない類似度は判断に使えない。"""
    path = build(tmp_path, ROWS)
    top = search_similar({"敷地面積坪": 50.0, "前面道路方向": "南"},
                         path=path)["results"][0]
    assert "敷地面積坪" in top["matched_on"]
    assert "前面道路方向" in top["matched_on"]


def test_件数を絞れる(tmp_path):
    path = build(tmp_path, ROWS)
    assert len(search_similar({"敷地面積坪": 50.0}, limit=1, path=path)["results"]) == 1


def test_比較できない項目は無視される(tmp_path):
    """指定した項目が空の物件でも、他の項目で比較する。"""
    rows = [["P0001", 2023, "2階", 112.6, None, 165.3, None, None, "南", "長方形"]]
    result = search_similar({"敷地面積坪": 50.0, "間口m": 9.0},
                            path=build(tmp_path, rows))
    assert result["comparable"] == 1
    assert result["results"][0]["matched_on"] == ["敷地面積坪"]


def test_条件が空なら拒否される(tmp_path):
    with pytest.raises(PlanError, match="検索条件が指定されていません"):
        search_similar({}, path=build(tmp_path, ROWS))
    with pytest.raises(PlanError, match="検索条件"):
        search_similar({"間口m": None}, path=build(tmp_path, ROWS))


def test_成立を保証しないと明記される(tmp_path):
    """似た事例があることと、その敷地で建つことは別。"""
    caveat = search_similar({"敷地面積坪": 50.0}, path=build(tmp_path, ROWS))["caveat"]
    assert "成立することを意味しない" in caveat
    assert "法規・地盤・予算" in caveat


def test_登録ゼロでも落ちない(tmp_path):
    result = search_similar({"敷地面積坪": 50.0}, path=build(tmp_path, []))
    assert result["total_plans"] == 0
    assert result["results"] == []


# ---------------------------------------------------------------- 登録状況


def test_項目の埋まり具合を返す(tmp_path):
    rows = [
        ["P0001", 2023, "2階", 112.6, None, 165.3, None, 9.1, "南", "長方形"],
        ["P0002", None, None, 92.3, None, 231.4, None, None, None, None],
    ]
    result = stats(build(tmp_path, rows))
    assert result["total"] == 2
    assert result["coverage"]["延床面積㎡"] == 100
    assert result["coverage"]["間口m"] == 50
    assert "間口m" in result["sparse_fields"]


def test_登録ゼロなら入力方法を案内する(tmp_path):
    result = stats(build(tmp_path, []))
    assert result["total"] == 0
    assert "過去の図面・見積・契約書から拾い出して" in result["note"]


def test_主軸の項目を優先するよう促す(tmp_path):
    note = stats(build(tmp_path, ROWS))["note"]
    assert "敷地面積坪" in note and "類似検索の主軸" in note


# ------------------------------------------------------ 書き込み(拾い出し)

import shutil  # noqa: E402 - 書き込みのテストだけで使う

from ai_employee.plans import (  # noqa: E402
    DEFAULT_DB,
    describe_schema,
    record_plan,
)


@pytest.fixture
def テンプレート(tmp_path):
    """事務所のテンプレートを複製する。

    数式・入力規則は本物でないと意味がないので、簡易版では代用しない。
    """
    if not DEFAULT_DB.is_file():
        pytest.skip("テンプレートが見つかりません")
    path = tmp_path / "db.xlsx"
    shutil.copy(DEFAULT_DB, path)
    return path


def test_図面から読めた項目だけを書き込める(テンプレート):
    result = record_plan("P0001", {
        "階数": "2階", "延床面積㎡": 115.92, "間口m": 10.92, "前面道路方向": "南",
    }, path=テンプレート)

    plan = load_plans(テンプレート)[0]
    assert plan.plan_id == "P0001"
    assert plan.get("間口m") == 10.92
    # 渡さなかった項目は空のまま。推測で埋めない。
    assert plan.get("敷地面積㎡") in (None, "")
    assert result["reference_case"] is False


def test_数式の列には書き込めない(テンプレート):
    with pytest.raises(PlanError, match="数式"):
        record_plan("P0001", {"延床面積坪": 35.0}, path=テンプレート)


def test_坪は平米から補って読める(テンプレート):
    # Excel を開くまで数式は計算されない。検索の主軸なので落とさない。
    record_plan("P0001", {"延床面積㎡": 115.92}, path=テンプレート)
    assert load_plans(テンプレート)[0].get("延床面積坪") == 35.1


def test_選択肢にない値は拒否される(テンプレート):
    with pytest.raises(PlanError, match="平屋"):
        record_plan("P0001", {"階数": "二階建て"}, path=テンプレート)


def test_同名の列はシート名なしでは書けない(テンプレート):
    # ②の「LDK帖」は要望、③は実績。どちらの意味かは機械には決められない。
    with pytest.raises(PlanError, match="複数のシート"):
        record_plan("P0001", {"LDK帖": 17.3}, path=テンプレート)

    record_plan("P0001", {"③部屋・面積.LDK帖": 17.3}, path=テンプレート)
    assert load_plans(テンプレート)[0].get("LDK帖") == 17.3


def test_知らない列は拒否される(テンプレート):
    with pytest.raises(PlanError, match="という列はありません"):
        record_plan("P0001", {"日当たりの良さ": "良い"}, path=テンプレート)


def test_個人情報は書き込めない(テンプレート):
    # 「使い方」シートの指示——顧客名・住所・電話番号を入れない——を機械側で守る。
    for 値 in ["山田様", "090-1234-5678", "〒500-8000", "3丁目5番地",
               "test@example.com"]:
        with pytest.raises(PlanError, match="入力できません"):
            record_plan("P0001", {"備考": 値}, path=テンプレート)
    assert load_plans(テンプレート) == []


def test_値が一つでも撥ねられたら何も書かれない(テンプレート):
    with pytest.raises(PlanError):
        record_plan("P0001", {"間口m": 10.92, "階数": "二階建て"}, path=テンプレート)
    assert load_plans(テンプレート) == []


def test_既存の物件IDは黙って上書きしない(テンプレート):
    record_plan("P0001", {"間口m": 10.92}, path=テンプレート)
    with pytest.raises(PlanError, match="既に登録"):
        record_plan("P0001", {"間口m": 8.0}, path=テンプレート)

    record_plan("P0001", {"間口m": 8.0}, path=テンプレート, replace=True)
    plans = load_plans(テンプレート)
    assert len(plans) == 1
    assert plans[0].get("間口m") == 8.0


def test_物件IDが空なら拒否される(テンプレート):
    with pytest.raises(PlanError, match="物件ID"):
        record_plan("  ", {"間口m": 10.92}, path=テンプレート)


def test_書き込む値がなければ拒否される(テンプレート):
    with pytest.raises(PlanError, match="書き込む値がありません"):
        record_plan("P0001", {}, path=テンプレート)


# ------------------------------------------------------------ 自社事例と参考事例


def test_出典をつけると参考事例になる(テンプレート):
    result = record_plan("P0001", {"延床面積㎡": 115.92},
                         source="他社の販売図面", path=テンプレート)
    assert result["reference_case"] is True

    plan = load_plans(テンプレート)[0]
    assert plan.get("参考事例") is True
    assert plan.get("出典") == "他社の販売図面"


def test_出典がなければ自社事例として扱う(テンプレート):
    record_plan("P0001", {"延床面積㎡": 115.92}, path=テンプレート)
    assert load_plans(テンプレート)[0].get("参考事例") is False


def test_出典と備考は同時に指定できない(テンプレート):
    # 両方を受けると片方が黙って消える。消える側があるなら断る。
    with pytest.raises(PlanError, match="同時に指定できません"):
        record_plan("P0001", {"備考": "角地"}, source="他社の販売図面",
                    path=テンプレート)


def test_自社事例だけで探せる(テンプレート):
    record_plan("P0001", {"延床面積㎡": 115.92}, path=テンプレート)
    record_plan("P0002", {"延床面積㎡": 115.92}, source="他社の販売図面",
                path=テンプレート)

    両方 = search_similar({"延床面積坪": 35.0}, path=テンプレート)
    assert {r["plan_id"] for r in 両方["results"]} == {"P0001", "P0002"}
    assert 両方["reference_hits"] == ["P0002"]
    assert "他社" in 両方["caveat"]

    自社 = search_similar({"延床面積坪": 35.0}, path=テンプレート,
                          include_reference=False)
    assert [r["plan_id"] for r in 自社["results"]] == ["P0001"]
    assert 自社["excluded_reference"] == 1
    assert "他社" not in 自社["caveat"]


def test_参考事例の印は充実度に数えない(テンプレート):
    record_plan("P0001", {"延床面積㎡": 115.92}, path=テンプレート)
    result = stats(テンプレート)
    assert "参考事例" not in result["coverage"]
    assert "出典" not in result["coverage"]
    assert result["reference_cases"] == []


def test_充実度は参考事例の件数を知らせる(テンプレート):
    record_plan("P0001", {"延床面積㎡": 115.92}, source="他社の販売図面",
                path=テンプレート)
    result = stats(テンプレート)
    assert result["reference_cases"] == ["P0001"]
    assert "他社" in result["note"]


# ---------------------------------------------------------------- 列の一覧


def test_入力できる列と選択肢を示す(テンプレート):
    described = describe_schema(テンプレート)
    基本 = {c["列"]: c for c in described["sheets"]["①物件基本情報"]}
    assert 基本["階数"]["選択肢"] == ["平屋", "2階", "3階"]
    assert 基本["間口m"]["選択肢"] is None
    assert 基本["階数"]["重複"] is False


def test_数式の列は入力欄として案内しない(テンプレート):
    described = describe_schema(テンプレート)
    assert "①物件基本情報.延床面積坪" in described["computed"]
    assert "③部屋・面積.収納合計帖" in described["computed"]
    for columns in described["sheets"].values():
        assert "延床面積坪" not in [c["列"] for c in columns]


def test_同名の列には印がつく(テンプレート):
    described = describe_schema(テンプレート)
    部屋 = {c["列"]: c for c in described["sheets"]["③部屋・面積"]}
    assert 部屋["LDK帖"]["重複"] is True


def test_物件ID列は入力欄に出ない(テンプレート):
    described = describe_schema(テンプレート)
    for columns in described["sheets"].values():
        assert KEY_COLUMN not in [c["列"] for c in columns]
