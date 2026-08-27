"""端末まわりのテスト——文字化けと出力エラーを防ぐ仕組み。

Windows の日本語コンソール(cp932)では ✓ ✗ ▸ █ — が出せない。
出せない環境では cp932 にもある記号へ落ちることを確認する。
"""

import importlib
import io
import sys

import pytest

from ai_employee import cli


def marks_for(encoding: str) -> dict[str, str]:
    """指定した文字コードの端末に見せかけて、選ばれる記号を得る。"""
    real = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
    try:
        importlib.reload(cli)
        return dict(cli.MARK)
    finally:
        sys.stdout = real
        importlib.reload(cli)


@pytest.mark.parametrize("encoding", ["utf-8", "cp932", "shift_jis", "euc_jp", "ascii"])
def test_選ばれた記号はその端末で必ず出せる(encoding):
    """ここが要。出せない記号を選ぶと print で落ちる。"""
    for mark in marks_for(encoding).values():
        mark.encode(encoding)


def test_UTF8ならそのままの記号を使う():
    assert marks_for("utf-8") == cli._FANCY_MARKS


def test_cp932では日本語環境で読める記号に落ちる():
    marks = marks_for("cp932")
    assert marks == cli._PLAIN_MARKS
    assert marks["ok"] == "○" and marks["ng"] == "×"


def test_ASCIIのみの端末でも記号が残る():
    marks = marks_for("ascii")
    assert all(mark.isascii() for mark in marks.values())


def test_出力の文字コードを勝手に変えない():
    """cp932 の端末を UTF-8 にすると、記号どころか日本語全体が化ける。"""
    real = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")
    try:
        cli._prepare_stdout()
        assert sys.stdout.encoding.lower().replace("-", "") == "cp932"
        # 落ちないように errors だけは緩める
        assert sys.stdout.errors == "replace"
    finally:
        sys.stdout = real


def test_色は無効にできる(capsys):
    cli.set_color(True)
    assert "\033[" in cli.BOLD("あ")
    cli.set_color(False)
    assert cli.BOLD("あ") == "あ"


def test_no_color_オプションで装飾を外せる(tmp_path, capsys):
    cli.set_color(True)
    cli.main(["--office", str(tmp_path), "--no-color", "post"])
    assert "\033[" not in capsys.readouterr().out


def test_NO_COLOR環境変数を尊重する(monkeypatch):
    """https://no-color.org/ の慣習に従う。"""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    importlib.reload(cli)
    assert cli._COLOR is False
    monkeypatch.delenv("NO_COLOR")
    importlib.reload(cli)


def test_パイプ出力では色を付けない():
    """ファイルに落としたときに制御文字が混ざらないように。"""
    importlib.reload(cli)
    assert cli._COLOR is False  # テスト実行時は tty ではない


# ------------------------------------------------------------ 入力の文字コード


@pytest.mark.parametrize(
    "encoding", ["utf-8", "cp932", "euc_jp", "iso2022_jp", "utf-16"]
)
def test_日本語の文字コードを自動で判別して読む(tmp_path, encoding):
    """Windows のメモ帳で保存した原稿は Shift_JIS のことがある。"""
    path = tmp_path / "draft.md"
    path.write_bytes("必ずご満足いただけます。".encode(encoding))
    text, used = cli.read_user_file(path)
    assert text.lstrip("\ufeff") == "必ずご満足いただけます。"
    assert used == encoding


def test_cp932として読めても中身が化けていれば採用しない(tmp_path):
    """cp932 はほぼどんなバイト列でもデコードに成功してしまう。

    「読めたかどうか」で判定すると、EUC-JP を cp932 として読んで
    自分で文字化けを作ることになる。
    """
    path = tmp_path / "draft.md"
    path.write_bytes("必ずご満足いただけます。".encode("euc_jp"))
    text, used = cli.read_user_file(path)
    assert used == "euc_jp"
    assert "ﾉ" not in text  # 半角カナの羅列 = 化けた状態


def test_BOM付きUTF8のBOMを取り除く(tmp_path):
    path = tmp_path / "draft.md"
    path.write_bytes("無垢の床".encode("utf-8-sig"))
    text, used = cli.read_user_file(path)
    assert text == "無垢の床"  # BOM が見出しに混ざらない
    assert used == "utf-8-sig"


def test_判別できないファイルでも落ちない(tmp_path):
    path = tmp_path / "broken.md"
    path.write_bytes(b"\xc0\xc1\xf5\xf6\xf7 broken")
    text, used = cli.read_user_file(path)
    assert "broken" in text
    assert "判別できず" in used


def test_化け具合を数値で判定できる():
    assert cli._japanese_score("必ずご満足いただけます。") == 1.0
    assert cli._japanese_score("ﾉｬ､ｺ､ｴﾋﾂｭ") < 0.5   # 誤った文字コードで読んだ状態
    assert cli._japanese_score("") == 0.0


def test_ShiftJISの原稿をチェックできる(tmp_path, capsys):
    draft = tmp_path / "draft.md"
    draft.write_bytes("必ずご満足いただけます。".encode("cp932"))
    assert cli.main(["--office", str(tmp_path), "check", "--file", str(draft)]) == 1
    out = capsys.readouterr().out
    assert "必ず" in out
    assert "cp932 として読みました" in out
