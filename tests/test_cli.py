import json

import pytest

from ai_employee.cli import main


def run(args, tmp_path):
    return main(["--office", str(tmp_path), *args])


def test_採用してから名簿に載る(tmp_path, capsys):
    assert run(["hire", "--id", "sato", "--name", "佐藤 AI", "--template", "sales"], tmp_path) == 0
    assert run(["roster"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "佐藤 AI" in out
    assert "営業担当" in out

    saved = json.loads((tmp_path / "sato" / "profile.json").read_text(encoding="utf-8"))
    assert saved["employee_id"] == "sato"


def test_ID_省略時は氏名から生成される(tmp_path):
    assert run(["hire", "--name", "Sato AI"], tmp_path) == 0
    assert (tmp_path / "sato-ai" / "profile.json").is_file()


def test_同じ_ID_の二重採用は拒否される(tmp_path, capsys):
    run(["hire", "--id", "sato", "--name", "佐藤"], tmp_path)
    assert run(["hire", "--id", "sato", "--name", "別人"], tmp_path) == 1
    assert "既に在籍" in capsys.readouterr().out
    assert run(["hire", "--id", "sato", "--name", "別人", "--force"], tmp_path) == 0


def test_採用時のオプションが職務定義書に反映される(tmp_path):
    run(["hire", "--id", "r", "--name", "調査 AI", "--role", "主任", "--web"], tmp_path)
    saved = json.loads((tmp_path / "r" / "profile.json").read_text(encoding="utf-8"))
    assert saved["role"] == "主任"
    assert saved["web_access"] is True


def test_在籍者がいなければその旨を伝える(tmp_path, capsys):
    assert run(["roster"], tmp_path) == 0
    assert "在籍者はいません" in capsys.readouterr().out


def test_未採用の社員への操作はエラー終了(tmp_path, capsys):
    assert run(["tasks", "--id", "nobody"], tmp_path) == 1
    assert "在籍していません" in capsys.readouterr().err


def test_タスクとメモを一覧できる(tmp_path, capsys):
    from ai_employee.workspace import Workspace

    run(["hire", "--id", "sato", "--name", "佐藤"], tmp_path)
    ws = Workspace("sato", tmp_path)
    ws.add_task("見積作成", due="2026-09-30")
    ws.add_note("商談", "A社と面談", ["a社"])

    assert run(["tasks", "--id", "sato"], tmp_path) == 0
    assert "見積作成" in capsys.readouterr().out
    assert run(["notes", "--id", "sato", "--query", "A社"], tmp_path) == 0
    assert "A社と面談" in capsys.readouterr().out


def test_職種テンプレートを一覧できる(tmp_path, capsys):
    assert run(["templates"], tmp_path) == 0
    assert "bim" in capsys.readouterr().out


def test_不正な職種は引数解析で弾かれる(tmp_path):
    with pytest.raises(SystemExit):
        run(["hire", "--name", "X", "--template", "ninja"], tmp_path)


# ------------------------------------------------------- 陣容と案件台帳


def test_標準陣容を一括採用できる(tmp_path, capsys):
    assert run(["hire-team"], tmp_path) == 0
    out = capsys.readouterr().out
    for employee_id in ("shukyaku", "eigyo", "marke", "jimu", "bim"):
        assert (tmp_path / employee_id / "profile.json").is_file()
        assert employee_id in out
    assert "5 名を採用しました" in out


def test_一括採用は既存社員を飛ばす(tmp_path, capsys):
    run(["hire", "--id", "eigyo", "--name", "既存の営業"], tmp_path)
    assert run(["hire-team"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "見送り" in out and "eigyo" in out
    assert "4 名を採用しました" in out

    saved = json.loads((tmp_path / "eigyo" / "profile.json").read_text(encoding="utf-8"))
    assert saved["name"] == "既存の営業"  # 上書きされていない


def seed(tmp_path):
    from ai_employee.company import ProjectLedger

    ledger = ProjectLedger(tmp_path)
    pj = ledger.add(
        "田中邸 新築", "田中様", "戸建住宅", source="HP問い合わせ", owner="eigyo", by="shukyaku"
    )
    ledger.update(
        pj["id"], "初回相談を実施", by="eigyo", stage="初回相談", next_due="2026-09-05"
    )
    return pj["id"]


def test_案件台帳を一覧できる(tmp_path, capsys):
    seed(tmp_path)
    assert run(["projects"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "田中邸 新築" in out
    assert "初回相談" in out


def test_案件をステージと担当で絞り込める(tmp_path, capsys):
    seed(tmp_path)
    assert run(["projects", "--owner", "bim"], tmp_path) == 0
    assert "該当する案件はありません" in capsys.readouterr().out
    assert run(["projects", "--stage", "初回相談"], tmp_path) == 0
    assert "田中邸 新築" in capsys.readouterr().out


def test_パイプラインを表示できる(tmp_path, capsys):
    seed(tmp_path)
    assert run(["projects", "--pipeline"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "進行中案件 1 件" in out
    assert "初回相談" in out


def test_案件の経緯を追える(tmp_path, capsys):
    project_id = seed(tmp_path)
    assert run(["project", project_id], tmp_path) == 0
    out = capsys.readouterr().out
    assert "田中様" in out
    assert "shukyaku" in out and "案件を登録した" in out
    assert "eigyo" in out and "初回相談を実施" in out


def test_存在しない案件の照会はエラー終了(tmp_path, capsys):
    assert run(["project", "ffffffff"], tmp_path) == 1
    assert "案件が見つかりません" in capsys.readouterr().err


# --------------------------------------------- 事務所プロフィールと集客


def test_事務所プロフィールの雛形を作れる(tmp_path, capsys):
    assert run(["office"], tmp_path) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "_company" / "office.json").is_file()
    assert "雛形を作成しました" in out
    assert "作り話を防ぐため" in out


def test_事務所プロフィールを設定できる(tmp_path, capsys):
    assert run(
        ["office", "--name", "アペックス設計事務所", "--areas", "東京23区, 川崎市"],
        tmp_path,
    ) == 0
    out = capsys.readouterr().out
    assert "アペックス設計事務所" in out
    assert "東京23区、川崎市" in out

    saved = json.loads((tmp_path / "_company" / "office.json").read_text(encoding="utf-8"))
    assert saved["areas"] == ["東京23区", "川崎市"]  # 前後の空白は落とす


def test_事務所プロフィールを表示できる(tmp_path, capsys):
    run(["office", "--name", "A設計"], tmp_path)
    capsys.readouterr()
    assert run(["office", "--show"], tmp_path) == 0
    assert "A設計" in capsys.readouterr().out


def seed_lead(tmp_path):
    import json as _json
    from datetime import timedelta

    from ai_employee.company import ProjectLedger
    from ai_employee.workspace import now

    ledger = ProjectLedger(tmp_path)
    ledger.add("田中様 新築", source="HP問い合わせ", owner="shukyaku", by="shukyaku")
    won = ledger.add("伊藤様 二世帯", source="紹介", owner="shukyaku", by="shukyaku")
    ledger.update(won["id"], "設計契約", status="won")

    data = _json.loads(ledger.path.read_text(encoding="utf-8"))
    for project in data:
        if project["name"].startswith("田中"):
            project["updated_at"] = (now() - timedelta(days=30)).isoformat(timespec="seconds")
    ledger.path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_追客が止まっている案件を洗い出せる(tmp_path, capsys):
    seed_lead(tmp_path)
    assert run(["stale"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "田中様 新築" in out
    assert "伊藤様 二世帯" not in out  # 受注済みは追客対象外


def test_放置がなければその旨を伝える(tmp_path, capsys):
    seed_lead(tmp_path)
    assert run(["stale", "--days", "60"], tmp_path) == 0
    assert "動いていない進行中案件はありません" in capsys.readouterr().out


def test_流入経路別に集計できる(tmp_path, capsys):
    seed_lead(tmp_path)
    assert run(["sources"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "HP問い合わせ" in out
    assert "紹介" in out
    assert "母数少" in out  # 決着 5 件未満は断定させない


def test_案件がなければ集計できないと伝える(tmp_path, capsys):
    assert run(["sources"], tmp_path) == 0
    assert "集計できる案件がありません" in capsys.readouterr().out


# --------------------------------------------------------------- 営業


def setup_sales(tmp_path):
    from ai_employee.company import ProjectLedger

    run(
        [
            "office", "--name", "A設計",
            "--unit-prices", "戸建住宅:80-100",
            "--design-fee-rate", "10",
            "--design-fee-minimum", "300",
        ],
        tmp_path,
    )
    return ProjectLedger(tmp_path).add("佐々木様 新築", "佐々木様", "戸建住宅", owner="eigyo")


def test_坪単価と料率を設定できる(tmp_path):
    setup_sales(tmp_path)
    saved = json.loads((tmp_path / "_company" / "office.json").read_text(encoding="utf-8"))
    assert saved["unit_prices"] == {"戸建住宅": [80, 100]}
    assert saved["design_fee_rate"] == 10
    assert saved["design_fee_minimum"] == 300


@pytest.mark.parametrize(
    "raw", ["戸建住宅", "戸建住宅:80", "戸建住宅:100-80", "宇宙船:80-100", "戸建住宅:安-高"]
)
def test_坪単価の書式不正は弾かれる(tmp_path, raw):
    assert run(["office", "--unit-prices", raw], tmp_path) == 1


def test_概算を算定できる(tmp_path, capsys):
    setup_sales(tmp_path)
    capsys.readouterr()
    assert run(["estimate", "--kind", "戸建住宅", "--tsubo", "35"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "2,800〜3,500 万円" in out
    assert "確定金額ではない" in out


def test_単価未設定の用途は概算を出さずエラー終了(tmp_path, capsys):
    setup_sales(tmp_path)
    capsys.readouterr()
    assert run(["estimate", "--kind", "共同住宅", "--tsubo", "100"], tmp_path) == 1
    assert "未設定" in capsys.readouterr().err


def test_ヒアリング状況を確認できる(tmp_path, capsys):
    from ai_employee.company import ProjectLedger

    project = setup_sales(tmp_path)
    ProjectLedger(tmp_path).record_hearing(
        project["id"], {"budget": "総額4500万円"}, by="eigyo"
    )
    capsys.readouterr()
    assert run(["hearing", project["id"]], tmp_path) == 0
    out = capsys.readouterr().out
    assert "総額4500万円" in out
    assert "決裁者" in out
    assert "提案より先に確認してください" in out


def test_必須が揃えば提案可と表示される(tmp_path, capsys):
    from ai_employee.company import HEARING_REQUIRED, ProjectLedger

    project = setup_sales(tmp_path)
    ProjectLedger(tmp_path).record_hearing(
        project["id"], {k: "確認済み" for k in HEARING_REQUIRED}, by="eigyo"
    )
    capsys.readouterr()
    assert run(["hearing", project["id"]], tmp_path) == 0
    assert "提案に進めます" in capsys.readouterr().out


# ------------------------------------------------------- マーケティング


def seed_marketing(tmp_path):
    from ai_employee.company import ProjectLedger

    ledger = ProjectLedger(tmp_path)
    ok = ledger.add("田中邸 新築", "田中様", "戸建住宅", by="shukyaku")
    ledger.add("山本邸 平屋", "山本様", "戸建住宅", by="shukyaku")
    ledger.record_consent(ok["id"], "条件付き", "施主名は伏せる。外観写真のみ。", by="marke")
    return ok["id"]


def test_許諾状態を確認できる(tmp_path, capsys):
    project_id = seed_marketing(tmp_path)
    assert run(["consent", project_id], tmp_path) == 0
    out = capsys.readouterr().out
    assert "条件付き" in out
    assert "施主名は伏せる" in out


def test_許諾を記録できる(tmp_path, capsys):
    project_id = seed_marketing(tmp_path)
    assert run(["consent", project_id, "--status", "許諾済"], tmp_path) == 0
    assert "許諾済" in capsys.readouterr().out


def test_条件なしの条件付き許諾はエラー終了(tmp_path, capsys):
    project_id = seed_marketing(tmp_path)
    assert run(["consent", project_id, "--status", "条件付き"], tmp_path) == 1
    assert "条件の記載が必須" in capsys.readouterr().err


def test_発信ネタを棚卸しできる(tmp_path, capsys):
    seed_marketing(tmp_path)
    assert run(["candidates"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "田中邸 新築" in out
    assert "先に施主の許諾が必要な案件" in out
    assert "山本邸 平屋" in out


def test_原稿の表現をチェックできる(tmp_path, capsys):
    assert run(["check", "--text", "地域No.1の設計事務所です"], tmp_path) == 1
    out = capsys.readouterr().out
    assert "No.1" in out
    assert "適法性の判断ではない" in out


def test_指摘がなければ正常終了(tmp_path, capsys):
    assert run(["check", "--text", "木の質感を生かした住まいです。"], tmp_path) == 0
    assert "指摘はありません" in capsys.readouterr().out


def test_ファイルからチェックできる(tmp_path, capsys):
    draft = tmp_path / "draft.md"
    draft.write_text("必ずご満足いただけます。", encoding="utf-8")
    assert run(["check", "--file", str(draft)], tmp_path) == 1
    assert "必ず" in capsys.readouterr().out


def test_チェック対象は本文かファイルのどちらか(tmp_path):
    with pytest.raises(SystemExit):
        run(["check"], tmp_path)
    with pytest.raises(SystemExit):
        run(["check", "--text", "a", "--file", "b"], tmp_path)


# ------------------------------------------------------------------ 事務


def setup_billing_office(tmp_path):
    from ai_employee.company import ProjectLedger

    run(
        [
            "office", "--name", "A設計",
            "--billing-schedule",
            "契約金:30:設計契約,基本設計完了:30:基本設計,実施設計完了:30:実施設計,引渡:10:竣工",
        ],
        tmp_path,
    )
    ledger = ProjectLedger(tmp_path)
    project = ledger.add("田中邸 新築", "田中様", "戸建住宅", owner="eigyo")
    ledger.update(project["id"], "実施設計に着手", stage="実施設計")
    return project["id"]


def test_請求スケジュールを設定できる(tmp_path):
    setup_billing_office(tmp_path)
    saved = json.loads((tmp_path / "_company" / "office.json").read_text(encoding="utf-8"))
    assert saved["billing_schedule"][0] == {"label": "契約金", "ratio": 30, "stage": "設計契約"}


@pytest.mark.parametrize(
    "raw",
    [
        "契約金:30",                      # 項目数が足りない
        "契約金:30:着工前",                # 不正なステージ
        "契約金:半分:設計契約",             # 割合が整数でない
        "契約金:50:設計契約",              # 合計が 100% でない
        "契約金:0:設計契約,引渡:100:竣工",  # 割合が 0
    ],
)
def test_請求スケジュールの書式不正は弾かれる(tmp_path, raw):
    assert run(["office", "--billing-schedule", raw], tmp_path) == 1


def test_請求計画を作成して明細を見られる(tmp_path, capsys):
    project_id = setup_billing_office(tmp_path)
    capsys.readouterr()
    assert run(["billing", "--project", project_id, "--setup", "1000001"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "1,000,001 円" in out
    assert "端数 1 円を調整" in out
    assert "未請求" in out


def test_請求計画がなければその旨を伝える(tmp_path, capsys):
    project_id = setup_billing_office(tmp_path)
    capsys.readouterr()
    assert run(["billing", "--project", project_id], tmp_path) == 0
    assert "請求計画は未作成です" in capsys.readouterr().out


def test_請求と入金を記録できる(tmp_path, capsys):
    project_id = setup_billing_office(tmp_path)
    run(["billing", "--project", project_id, "--setup", "1000000"], tmp_path)
    capsys.readouterr()
    assert run(["billing", "--project", project_id, "--paid", "m1"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "入金済" in out
    assert "入金済 300,000" in out


def test_請求漏れを洗い出せる(tmp_path, capsys):
    project_id = setup_billing_office(tmp_path)
    run(["billing", "--project", project_id, "--setup", "1000000"], tmp_path)
    capsys.readouterr()
    assert run(["billing", "--alerts"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "請求漏れの疑い 3 件" in out
    assert "900,000 円" in out


def test_漏れがなければその旨を伝える(tmp_path, capsys):
    setup_billing_office(tmp_path)
    capsys.readouterr()
    assert run(["billing", "--alerts"], tmp_path) == 0
    assert "請求漏れ・入金遅延" in capsys.readouterr().out


def test_全案件の請求状況を横断で見られる(tmp_path, capsys):
    project_id = setup_billing_office(tmp_path)
    run(["billing", "--project", project_id, "--setup", "1000000"], tmp_path)
    capsys.readouterr()
    assert run(["billing"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "田中邸 新築" in out
    assert "合計" in out


def test_請求計画のある案件がなければ伝える(tmp_path, capsys):
    setup_billing_office(tmp_path)
    capsys.readouterr()
    assert run(["billing"], tmp_path) == 0
    assert "請求計画のある案件がありません" in capsys.readouterr().out


# -------------------------------------------------------------- 土地診断


def test_単発で土地診断できる(tmp_path, capsys):
    assert run(
        ["land", "--site-area", "132.5", "--zoning", "第一種低層住居専用地域",
         "--coverage", "50", "--far", "100", "--road-width", "4.0", "--road-contact", "6.2"],
        tmp_path,
    ) == 0
    out = capsys.readouterr().out
    assert "66.25 ㎡" in out
    assert "接道義務: 満たしている" in out
    assert "この診断では判定していない項目" in out
    assert "法適合の判断ではない" in out


def test_道路幅員で容積率が制限される(tmp_path, capsys):
    assert run(
        ["land", "--site-area", "200", "--zoning", "商業地域",
         "--coverage", "80", "--far", "400", "--road-width", "4.5"],
        tmp_path,
    ) == 0
    out = capsys.readouterr().out
    assert "540.0 ㎡" in out          # 200 × 270%
    assert "4.5m × 6/10 = 270.0%" in out


def test_必須項目が足りなければエラー終了(tmp_path, capsys):
    assert run(["land", "--site-area", "100"], tmp_path) == 1
    err = capsys.readouterr().err
    assert "用途地域" in err
    assert "推測しないこと" in err


def test_案件に紐づけて診断できる(tmp_path, capsys):
    from ai_employee.company import ProjectLedger

    project = ProjectLedger(tmp_path).add("佐々木様 新築", owner="eigyo")
    assert run(
        ["land", "--project", project["id"], "--site-area", "132.5",
         "--zoning", "第一種低層住居専用地域", "--coverage", "50", "--far", "100"],
        tmp_path,
    ) == 0
    capsys.readouterr()

    # 記録済みなので条件を指定せず再診断できる
    assert run(["land", "--project", project["id"]], tmp_path) == 0
    out = capsys.readouterr().out
    assert "佐々木様 新築" in out
    assert "66.25 ㎡" in out


def test_敷地条件未記録の案件はエラー終了(tmp_path, capsys):
    from ai_employee.company import ProjectLedger

    project = ProjectLedger(tmp_path).add("佐々木様 新築")
    assert run(["land", "--project", project["id"]], tmp_path) == 1
    assert "敷地条件が未記録" in capsys.readouterr().err


def test_緩和を指定すると要確認と明記される(tmp_path, capsys):
    assert run(
        ["land", "--site-area", "150", "--zoning", "商業地域", "--coverage", "80",
         "--far", "400", "--relaxation", "corner_lot"],
        tmp_path,
    ) == 0
    out = capsys.readouterr().out
    assert "建蔽率 90.0%" in out
    assert "行政に要確認" in out
