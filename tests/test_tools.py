import json

import pytest

from ai_employee.tools import ToolBox, build_tools


def test_権限のないツールは公開されない(workspace):
    box = ToolBox(workspace, ["record_note", "list_tasks"])
    assert [s["name"] for s in box.specs()] == ["record_note", "list_tasks"]


def test_未知のツール名を権限に書くと採用時に落ちる(workspace):
    with pytest.raises(ValueError, match="未知のツール"):
        ToolBox(workspace, ["launch_missiles"])


def test_ツールの並び順は定義順で安定する(workspace):
    """プロンプトキャッシュの前方一致を壊さないため、指定順に依存しない。"""
    a = ToolBox(workspace, ["list_tasks", "record_note"]).specs()
    b = ToolBox(workspace, ["record_note", "list_tasks"]).specs()
    assert a == b


def test_web権限があるときだけ_web_search_が付く(workspace):
    without = ToolBox(workspace, ["list_tasks"], web_access=False).specs()
    with_web = ToolBox(workspace, ["list_tasks"], web_access=True).specs()
    assert len(with_web) == len(without) + 1
    assert with_web[-1]["type"] == "web_search_20260209"


def test_権限外のツールを呼ぶとエラーとして返る(workspace):
    box = ToolBox(workspace, ["list_tasks"])
    output, is_error = box.run("write_file", {"path": "a", "content": "b"})
    assert is_error
    assert "権限がありません" in output


def test_ツール実行結果は_JSON_で返る(workspace):
    box = ToolBox(workspace, ["record_note", "search_notes"])
    box.run("record_note", {"title": "商談", "body": "A社", "tags": ["a社"]})
    output, is_error = box.run("search_notes", {"query": "A社"})
    assert not is_error
    assert json.loads(output)["count"] == 1


def test_引数不足は例外ではなくエラー結果になる(workspace):
    output, is_error = ToolBox(workspace, ["record_note"]).run("record_note", {})
    assert is_error
    assert "引数が不正" in output


def test_ワークスペース違反はエラー結果になる(workspace):
    box = ToolBox(workspace, ["read_file"])
    output, is_error = box.run("read_file", {"path": "../../etc/passwd"})
    assert is_error
    assert "ワークスペース外" in output


def test_全ツールに説明と_schema_がある(workspace):
    for tool in build_tools(workspace).values():
        assert tool.description.strip()
        assert tool.input_schema["additionalProperties"] is False
        for required in tool.input_schema["required"]:
            assert required in tool.input_schema["properties"]


def test_current_datetime_は日付情報を返す(workspace):
    output, is_error = ToolBox(workspace, ["current_datetime"]).run(
        "current_datetime", {}
    )
    assert not is_error
    assert set(json.loads(output)) == {"iso", "date", "time", "weekday"}


# ------------------------------------------------------- 案件台帳ツール


def test_案件ツールは台帳を共有する(workspace, tmp_path):
    """別の社員が起こした案件を、他の社員がそのまま見られる。"""
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox
    from ai_employee.workspace import Workspace

    ledger = ProjectLedger(tmp_path)
    shukyaku = ToolBox(Workspace("shukyaku", tmp_path), ["add_project"], ledger=ledger)
    eigyo = ToolBox(Workspace("eigyo", tmp_path), ["list_projects"], ledger=ledger)

    shukyaku.run("add_project", {"name": "田中邸 新築", "source": "HP問い合わせ"})
    output, is_error = eigyo.run("list_projects", {})
    assert not is_error
    assert json.loads(output)["projects"][0]["name"] == "田中邸 新築"


def test_案件登録は起票した社員を担当と履歴に記録する(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["add_project", "get_project"], ledger=ledger)
    created = json.loads(box.run("add_project", {"name": "田中邸 新築"})[0])
    assert created["owner"] == workspace.employee_id
    assert created["history"][0]["by"] == workspace.employee_id


def test_一覧は履歴を含めず全文は_get_project_で取る(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(
        workspace, ["add_project", "list_projects", "get_project"], ledger=ProjectLedger(tmp_path)
    )
    pid = json.loads(box.run("add_project", {"name": "田中邸 新築"})[0])["id"]
    listed = json.loads(box.run("list_projects", {})[0])["projects"][0]
    assert "history" not in listed
    assert json.loads(box.run("get_project", {"project_id": pid})[0])["history"]


def test_更新理由なしの案件更新はエラー結果になる(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(workspace, ["add_project", "update_project"], ledger=ProjectLedger(tmp_path))
    pid = json.loads(box.run("add_project", {"name": "田中邸 新築"})[0])["id"]
    output, is_error = box.run("update_project", {"project_id": pid, "note": "", "stage": "見積"})
    assert is_error
    assert "更新理由" in output


def test_存在しない案件へのメモ紐付けは拒否される(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(workspace, ["record_note"], ledger=ProjectLedger(tmp_path))
    output, is_error = box.run(
        "record_note", {"title": "商談", "body": "面談", "project_id": "deadbeef"}
    )
    assert is_error
    assert "案件が見つかりません" in output


def test_メモを案件で絞り込める(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(
        workspace, ["add_project", "record_note", "search_notes"], ledger=ProjectLedger(tmp_path)
    )
    pid = json.loads(box.run("add_project", {"name": "田中邸 新築"})[0])["id"]
    box.run("record_note", {"title": "初回相談", "body": "予算未確認", "project_id": pid})
    box.run("record_note", {"title": "社内会議", "body": "案件と無関係"})

    hits = json.loads(box.run("search_notes", {"project_id": pid})[0])
    assert hits["count"] == 1
    assert hits["notes"][0]["title"] == "初回相談"


def test_パイプラインツールが集計を返す(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(workspace, ["add_project", "pipeline"], ledger=ProjectLedger(tmp_path))
    box.run("add_project", {"name": "田中邸 新築"})
    result = json.loads(box.run("pipeline", {})[0])
    assert result["active_total"] == 1
    assert result["by_stage"]["反響"] == 1


def test_追客漏れツールは最後の履歴も返す(workspace, tmp_path):
    """社員が「なぜ止まっているか」を判断できるようにするため。"""
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["stale_projects"], ledger=ledger)
    ledger.add("放置案件", source="HP問い合わせ", by="shukyaku")

    result = json.loads(box.run("stale_projects", {"days": 0})[0])
    assert result["threshold_days"] == 0
    assert result["count"] == 1
    assert result["projects"][0]["last_entry"] == "案件を登録した"
    assert "history" not in result["projects"][0]  # 全文は get_project で取る


def test_負の日数はエラー結果になる(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    box = ToolBox(workspace, ["stale_projects"], ledger=ProjectLedger(tmp_path))
    output, is_error = box.run("stale_projects", {"days": -5})
    assert is_error
    assert "0 以上" in output


def test_流入経路ツールが集計を返す(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["source_report"], ledger=ledger)
    ledger.add("A", source="Instagram")
    result = json.loads(box.run("source_report", {})[0])
    assert result["sources"][0]["source"] == "Instagram"


def _sales_box(workspace, tmp_path, allowed, office=None):
    from ai_employee.company import OfficeProfile, ProjectLedger
    from ai_employee.tools import ToolBox

    (office or OfficeProfile(
        name="A設計",
        unit_prices={"戸建住宅": [80, 100]},
        design_fee_rate=10,
        design_fee_minimum=300,
    )).save(tmp_path)
    ledger = ProjectLedger(tmp_path)
    return ToolBox(workspace, allowed, ledger=ledger), ledger


def test_ヒアリング記録は残りの未確認項目も返す(workspace, tmp_path):
    """社員が次に何を聞くべきかを、同じ結果の中で分かるようにする。"""
    box, ledger = _sales_box(workspace, tmp_path, ["record_hearing"])
    pid = ledger.add("佐々木様 新築")["id"]

    result = json.loads(
        box.run("record_hearing", {"project_id": pid, "budget": "総額4500万円"})[0]
    )
    assert result["requirements"]["budget"] == "総額4500万円"
    assert result["gaps"]["ready_for_proposal"] is False
    assert any(m["key"] == "decision_maker" for m in result["gaps"]["missing_required"])


def test_推測で埋めた項目はスキーマ外なら弾かれる(workspace, tmp_path):
    box, ledger = _sales_box(workspace, tmp_path, ["record_hearing"])
    pid = ledger.add("佐々木様 新築")["id"]
    output, is_error = box.run("record_hearing", {"project_id": pid, "mood": "前向き"})
    assert is_error
    assert "未知です" in output


def test_概算ツールは根拠と但し書きを返す(workspace, tmp_path):
    box, _ = _sales_box(workspace, tmp_path, ["estimate_cost"])
    result = json.loads(
        box.run("estimate_cost", {"kind": "戸建住宅", "floor_area_tsubo": 35})[0]
    )
    assert result["construction_cost"] == {"low": 2800, "high": 3500}
    assert "延床 35.0 坪" in result["basis"]
    assert "確定金額ではない" in result["caveat"]


def test_単価未設定の用途はエラー結果になり概算が出ない(workspace, tmp_path):
    box, _ = _sales_box(workspace, tmp_path, ["estimate_cost"])
    output, is_error = box.run(
        "estimate_cost", {"kind": "共同住宅", "floor_area_tsubo": 100}
    )
    assert is_error
    assert "未設定" in output
    assert "書いてはいけません" in output


def test_ヒアリング状況ツールが提案可否を返す(workspace, tmp_path):
    from ai_employee.company import HEARING_REQUIRED

    box, ledger = _sales_box(workspace, tmp_path, ["hearing_gaps"])
    pid = ledger.add("佐々木様 新築")["id"]
    assert json.loads(box.run("hearing_gaps", {"project_id": pid})[0])["ready_for_proposal"] is False

    ledger.record_hearing(pid, {k: "確認済み" for k in HEARING_REQUIRED})
    assert json.loads(box.run("hearing_gaps", {"project_id": pid})[0])["ready_for_proposal"] is True


# ------------------------------------------------------- マーケティング


def test_許諾のない案件の発信記録はエラー結果になる(workspace, tmp_path):
    """ツール経由でも、許諾を取らずに出したことにはできない。"""
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["log_publication"], ledger=ledger)
    pid = ledger.add("田中邸 新築")["id"]

    output, is_error = box.run(
        "log_publication", {"project_id": pid, "channel": "Instagram", "title": "投稿"}
    )
    assert is_error
    assert "掲載許諾が「未確認」" in output


def test_許諾状態ツールが指示文を返す(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["publication_status", "record_consent"], ledger=ledger)
    pid = ledger.add("田中邸 新築")["id"]

    before = json.loads(box.run("publication_status", {"project_id": pid})[0])
    assert before["publishable"] is False

    box.run(
        "record_consent",
        {"project_id": pid, "status": "条件付き", "conditions": "施主名は伏せる"},
    )
    after = json.loads(box.run("publication_status", {"project_id": pid})[0])
    assert after["publishable"] is True
    assert "施主名は伏せる" in after["guidance"]


def test_条件なしの条件付き許諾はエラー結果になる(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["record_consent"], ledger=ledger)
    pid = ledger.add("田中邸 新築")["id"]
    output, is_error = box.run(
        "record_consent", {"project_id": pid, "status": "条件付き"}
    )
    assert is_error
    assert "条件の記載が必須" in output


def test_ネタ棚卸しツールが許諾で仕分ける(workspace, tmp_path):
    from ai_employee.company import ProjectLedger
    from ai_employee.tools import ToolBox

    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["publication_candidates"], ledger=ledger)
    ok = ledger.add("許諾済の案件")
    ledger.add("未確認の案件")
    ledger.record_consent(ok["id"], "許諾済")

    result = json.loads(box.run("publication_candidates", {})[0])
    assert [p["name"] for p in result["ready"]] == ["許諾済の案件"]
    assert [p["name"] for p in result["needs_consent"]] == ["未確認の案件"]


def test_表現チェックツールが指摘と免責を返す(workspace, tmp_path):
    from ai_employee.tools import ToolBox

    box = ToolBox(workspace, ["review_copy"])
    result = json.loads(
        box.run("review_copy", {"text": "地域No.1の設計事務所です"})[0]
    )
    assert result["count"] == 1
    assert "適法性の判断ではない" in result["disclaimer"]


def test_空の原稿チェックはエラー結果になる(workspace):
    from ai_employee.tools import ToolBox

    output, is_error = ToolBox(workspace, ["review_copy"]).run("review_copy", {"text": "  "})
    assert is_error
    assert "原稿が空" in output


# ------------------------------------------------------------------ 事務


def _billing_box(workspace, tmp_path, allowed):
    from ai_employee.billing import EXAMPLE_SCHEDULE
    from ai_employee.company import OfficeProfile, ProjectLedger
    from ai_employee.tools import ToolBox

    OfficeProfile(
        name="A設計",
        billing_schedule=[{"label": l, "ratio": r, "stage": s} for l, r, s in EXAMPLE_SCHEDULE],
    ).save(tmp_path)
    ledger = ProjectLedger(tmp_path)
    return ToolBox(workspace, allowed, ledger=ledger), ledger


def test_請求計画ツールが割り付けを返す(workspace, tmp_path):
    box, ledger = _billing_box(workspace, tmp_path, ["setup_billing"])
    pid = ledger.add("田中邸 新築")["id"]
    result = json.loads(box.run("setup_billing", {"project_id": pid, "contract_amount": 1_000_001})[0])
    assert sum(m["amount"] for m in result["plan"]) == 1_000_001


def test_スケジュール未設定なら請求計画を作れない(workspace, tmp_path):
    """事務所の契約実態を推測して割り付けさせない。"""
    from ai_employee.company import OfficeProfile, ProjectLedger
    from ai_employee.tools import ToolBox

    OfficeProfile(name="A設計").save(tmp_path)
    ledger = ProjectLedger(tmp_path)
    box = ToolBox(workspace, ["setup_billing"], ledger=ledger)
    pid = ledger.add("田中邸 新築")["id"]
    output, is_error = box.run("setup_billing", {"project_id": pid, "contract_amount": 1_000_000})
    assert is_error
    assert "請求スケジュールが事務所プロフィールに未設定" in output


def test_請求漏れツールが理由つきで返す(workspace, tmp_path):
    box, ledger = _billing_box(workspace, tmp_path, ["setup_billing", "billing_alerts"])
    pid = ledger.add("田中邸 新築")["id"]
    box.run("setup_billing", {"project_id": pid, "contract_amount": 1_000_000})
    ledger.update(pid, "契約成立", stage="設計契約")

    result = json.loads(box.run("billing_alerts", {})[0])
    assert len(result["unbilled"]) == 1
    assert result["unbilled_amount"] == 300_000
    assert "設計契約" in result["unbilled"][0]["reason"]


def test_税込計算ツールは税率未設定なら算出しない(workspace, tmp_path):
    box, _ = _billing_box(workspace, tmp_path, ["tax_breakdown"])
    result = json.loads(box.run("tax_breakdown", {"amount": 1_000_000})[0])
    assert result["including_tax"] is None
    assert "未設定" in result["note"]


def test_横断集計ツールが合計を返す(workspace, tmp_path):
    box, ledger = _billing_box(workspace, tmp_path, ["setup_billing", "billing_overview"])
    pid = ledger.add("田中邸 新築")["id"]
    box.run("setup_billing", {"project_id": pid, "contract_amount": 1_000_000})
    result = json.loads(box.run("billing_overview", {})[0])
    assert result["totals"]["total"] == 1_000_000
    assert result["totals"]["unbilled"] == 1_000_000


# -------------------------------------------------------------- 土地診断


def _land_box(workspace, tmp_path, allowed, land_settings=None):
    from ai_employee.company import OfficeProfile, ProjectLedger
    from ai_employee.tools import ToolBox

    OfficeProfile(name="A設計", land_settings=land_settings or {}).save(tmp_path)
    ledger = ProjectLedger(tmp_path)
    return ToolBox(workspace, allowed, ledger=ledger), ledger


def test_敷地条件を記録して診断できる(workspace, tmp_path):
    box, ledger = _land_box(workspace, tmp_path, ["record_land", "diagnose_land"])
    pid = ledger.add("佐々木様 新築")["id"]

    box.run("record_land", {
        "project_id": pid, "site_area": 132.5, "zoning": "第一種低層住居専用地域",
        "building_coverage": 50, "floor_area_ratio": 100,
        "road_width": 4.0, "road_contact": 6.2,
    })
    result = json.loads(box.run("diagnose_land", {"project_id": pid})[0])
    assert result["building_area_max"] == 66.25
    assert result["road_check"]["passes"] is True


def test_敷地条件未記録なら診断がエラー結果になる(workspace, tmp_path):
    """調べていない土地の診断結果を作らせない。"""
    box, ledger = _land_box(workspace, tmp_path, ["diagnose_land"])
    pid = ledger.add("佐々木様 新築")["id"]
    output, is_error = box.run("diagnose_land", {"project_id": pid})
    assert is_error
    assert "敷地条件が未記録" in output
    assert "推測してはいけません" in output


def test_診断結果に確認事項と但し書きが必ず入る(workspace, tmp_path):
    box, ledger = _land_box(workspace, tmp_path, ["record_land", "diagnose_land"])
    pid = ledger.add("佐々木様 新築")["id"]
    box.run("record_land", {
        "project_id": pid, "site_area": 150, "zoning": "商業地域",
        "building_coverage": 80, "floor_area_ratio": 400,
    })
    result = json.loads(box.run("diagnose_land", {"project_id": pid})[0])
    assert len(result["required_confirmations"]) >= 10
    assert "法適合の判断ではない" in result["disclaimer"]


def test_不正な用途地域はエラー結果になる(workspace, tmp_path):
    box, ledger = _land_box(workspace, tmp_path, ["record_land"])
    pid = ledger.add("佐々木様 新築")["id"]
    output, is_error = box.run("record_land", {
        "project_id": pid, "site_area": 150, "zoning": "住宅地",
        "building_coverage": 50, "floor_area_ratio": 100,
    })
    assert is_error
    assert "不正な用途地域" in output
