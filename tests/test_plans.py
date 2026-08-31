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
