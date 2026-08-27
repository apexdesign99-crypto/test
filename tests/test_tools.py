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
