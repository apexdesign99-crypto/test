import json

from ai_employee.cli import main


def run(args, tmp_path):
    return main(["--office", str(tmp_path), *args])


def test_採用してから名簿に載る(tmp_path, capsys):
    assert run(["hire", "--id", "sato", "--name", "佐藤 AI", "--template", "sales"], tmp_path) == 0
    assert run(["roster"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "佐藤 AI" in out
    assert "営業アシスタント" in out

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
    assert "researcher" in capsys.readouterr().out


def test_不正な職種は引数解析で弾かれる(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        run(["hire", "--name", "X", "--template", "ninja"], tmp_path)
