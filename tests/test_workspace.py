import pytest

from ai_employee.workspace import Workspace, WorkspaceError, roster


def test_未採用の社員を読み込むとエラー(tmp_path):
    with pytest.raises(WorkspaceError, match="在籍していません"):
        Workspace("nobody", tmp_path).load_profile()


def test_メモは新しい順に返る(workspace):
    workspace.add_note("一件目", "古い", ["a"])
    workspace.add_note("二件目", "新しい", ["b"])
    titles = [n["title"] for n in workspace.search_notes()]
    assert titles == ["二件目", "一件目"]


def test_メモを本文とタグで絞り込める(workspace):
    workspace.add_note("商談", "A社と面談した", ["a社"])
    workspace.add_note("障害", "B社で不具合", ["b社"])
    assert len(workspace.search_notes(query="A社")) == 1
    assert len(workspace.search_notes(tag="b社")) == 1
    assert workspace.search_notes(query="A社", tag="b社") == []


def test_メモ検索は大文字小文字を区別しない(workspace):
    workspace.add_note("Meeting", "Kickoff with ACME")
    assert len(workspace.search_notes(query="acme")) == 1


def test_タスクの登録と完了(workspace):
    task = workspace.add_task("見積作成", "A社向け", due="2026-09-30")
    assert workspace.list_tasks("open") == [task]
    closed = workspace.close_task(task["id"], "送付済み")
    assert closed["status"] == "done"
    assert closed["result"] == "送付済み"
    assert workspace.list_tasks("open") == []
    assert len(workspace.list_tasks("all")) == 1


def test_完了済みタスクは二重に閉じられない(workspace):
    task = workspace.add_task("A")
    workspace.close_task(task["id"])
    with pytest.raises(WorkspaceError, match="既に done"):
        workspace.close_task(task["id"])


def test_存在しないタスクの完了はエラー(workspace):
    with pytest.raises(WorkspaceError, match="見つかりません"):
        workspace.close_task("deadbeef")


def test_不正なステータス(workspace):
    with pytest.raises(WorkspaceError):
        workspace.list_tasks("todo")


def test_ファイルの読み書きと一覧(workspace):
    workspace.write_file("reports/2026-08.md", "# 月次")
    assert workspace.read_file("reports/2026-08.md") == "# 月次"
    assert workspace.list_files() == ["reports/2026-08.md"]


def test_存在しないファイルの読み込みはエラー(workspace):
    with pytest.raises(WorkspaceError, match="存在しません"):
        workspace.read_file("missing.md")


@pytest.mark.parametrize("path", ["../escape.md", "../../etc/passwd", "/etc/passwd"])
def test_ワークスペース外への書き込みは拒否される(workspace, path):
    with pytest.raises(WorkspaceError, match="ワークスペース外"):
        workspace.write_file(path, "x")


def test_シンボリックリンク経由の脱出も拒否される(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace.ensure()
    (workspace.files_dir / "link").symlink_to(outside)
    with pytest.raises(WorkspaceError, match="ワークスペース外"):
        workspace.write_file("link/secret.md", "x")


def test_会話ログの保存と復元(workspace):
    assert workspace.load_session("2026-08-26") == []
    workspace.save_session([{"role": "user", "content": "こんにちは"}], "2026-08-26")
    assert workspace.load_session("2026-08-26")[0]["content"] == "こんにちは"


def test_在籍者一覧(workspace, tmp_path):
    assert [p.employee_id for p in roster(tmp_path)] == ["tester"]


def test_空の会社は空の名簿を返す(tmp_path):
    assert roster(tmp_path / "none") == []
