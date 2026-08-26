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
