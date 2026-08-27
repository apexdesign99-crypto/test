from types import SimpleNamespace

import pytest

from ai_employee.agent import Employee, Listener
from ai_employee.config import FALLBACK_BETA, MAX_TOOL_ITERATIONS
from conftest import FakeClient, call_tool, message, say


def make(workspace, profile, script) -> tuple[Employee, FakeClient]:
    client = FakeClient(script)
    return Employee(profile, workspace, client=client), client


class Recorder(Listener):
    def __init__(self) -> None:
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.tools: list[str] = []
        self.notices: list[str] = []

    def on_text(self, text): self.text.append(text)
    def on_thinking(self, text): self.thinking.append(text)
    def on_tool_call(self, name, arguments): self.tools.append(name)
    def on_notice(self, message): self.notices.append(message)


def test_ツールを使わない依頼はそのまま返る(workspace, profile):
    employee, client = make(workspace, profile, [say("承知しました。")])
    result = employee.work("おはよう")
    assert result.text == "承知しました。"
    assert result.stop_reason == "end_turn"
    assert result.tool_calls == []
    assert len(client.messages.calls) == 1


def test_ツール呼び出しが実行され結果が返される(workspace, profile):
    script = [
        call_tool("record_note", {"title": "商談", "body": "A社と面談"}),
        say("メモを記録しました。"),
    ]
    employee, client = make(workspace, profile, script)
    result = employee.work("商談内容を記録して")

    assert result.tool_calls == ["record_note"]
    assert result.text == "メモを記録しました。"
    assert [n["title"] for n in workspace.search_notes()] == ["商談"]

    # 2 回目のリクエストに tool_result が含まれている
    sent = client.messages.calls[1]["messages"]
    tool_result = sent[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["is_error"] is False


def test_並列ツール呼び出しは一つの_user_メッセージで返す(workspace, profile):
    parallel = message(
        [
            {"type": "tool_use", "id": "a", "name": "add_task", "input": {"title": "X"}},
            {"type": "tool_use", "id": "b", "name": "add_task", "input": {"title": "Y"}},
        ],
        stop_reason="tool_use",
    )
    employee, client = make(workspace, profile, [parallel, say("登録しました。")])
    employee.work("2 件登録して")

    results = client.messages.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert len(workspace.list_tasks("open")) == 2


def test_ツールの失敗は_is_error_で伝えられループは続く(workspace, profile):
    script = [
        call_tool("read_file", {"path": "../../etc/passwd"}),
        say("アクセスできませんでした。"),
    ]
    employee, client = make(workspace, profile, script)
    result = employee.work("パスワードを読んで")

    tool_result = client.messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "ワークスペース外" in tool_result["content"]
    assert result.ok


def test_拒否応答は例外にせず結果に載せる(workspace, profile):
    refusal = message(
        [{"type": "text", "text": ""}],
        stop_reason="refusal",
        stop_details=SimpleNamespace(type="refusal", explanation="対応できません"),
    )
    recorder = Recorder()
    client = FakeClient([refusal])
    employee = Employee(profile, workspace, client=client, listener=recorder)
    result = employee.work("危険な依頼")

    assert result.refusal == "対応できません"
    assert result.ok is False
    assert recorder.notices


def test_出力上限に達した場合は打ち切りとして報告する(workspace, profile):
    employee, _ = make(workspace, profile, [say("途中まで", stop_reason="max_tokens")])
    result = employee.work("長文を書いて")
    assert result.truncated is True
    assert result.ok is False


def test_pause_turn_は同じ会話のまま再開される(workspace, profile):
    script = [say("検索中", stop_reason="pause_turn"), say("完了しました。")]
    employee, client = make(workspace, profile, script)
    result = employee.work("調べて")
    assert result.text == "完了しました。"
    assert len(client.messages.calls) == 2


def test_ツール呼び出しの暴走は上限で止まる(workspace, profile):
    script = [
        call_tool("current_datetime", {}, tool_id=f"t{i}")
        for i in range(MAX_TOOL_ITERATIONS)
    ]
    recorder = Recorder()
    client = FakeClient(script)
    employee = Employee(profile, workspace, client=client, listener=recorder)
    result = employee.work("延々と時刻を見て")

    assert len(result.tool_calls) == MAX_TOOL_ITERATIONS
    assert any("上限" in n for n in recorder.notices)


def test_ストリーミングの本文と思考がリスナーに届く(workspace, profile):
    turn = message(
        [
            {"type": "thinking", "thinking": "整理する"},
            {"type": "text", "text": "できました。"},
        ]
    )
    recorder = Recorder()
    employee = Employee(profile, workspace, client=FakeClient([turn]), listener=recorder)
    employee.work("やって")
    assert "".join(recorder.text) == "できました。"
    assert "".join(recorder.thinking) == "整理する"


def test_ストリームは必ず閉じられる(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("やって")
    assert all(s.closed for s in client.messages.streams)


def test_会話履歴を引き継いで依頼できる(workspace, profile):
    employee, client = make(workspace, profile, [say("A社の件ですね。")])
    history = [
        {"role": "user", "content": "A社の話"},
        {"role": "assistant", "content": [{"type": "text", "text": "承知"}]},
    ]
    result = employee.work("続きを", history)
    assert client.messages.calls[0]["messages"][:2] == history
    assert len(result.messages) == 4  # 履歴 2 + 今回の user/assistant


def test_履歴は呼び出し元のリストを破壊しない(workspace, profile):
    employee, _ = make(workspace, profile, [say("はい")])
    history = [{"role": "user", "content": "前回"}]
    employee.work("今回", history)
    assert history == [{"role": "user", "content": "前回"}]


def test_リクエストの既定値(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("やって")
    kwargs = client.messages.calls[0]

    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"]["effort"] == profile.effort
    # 拒否時のサーバ側フォールバックを既定で有効化している
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"
    assert "budget_tokens" not in str(kwargs["thinking"])


def test_system_は不変部分にだけキャッシュ区切りを置く(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    workspace.add_task("見積作成")
    employee.work("やって")
    blocks = client.messages.calls[0]["system"]

    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "現在時刻" not in blocks[0]["text"]
    assert "cache_control" not in blocks[1]
    assert "見積作成" in blocks[1]["text"]  # 未完了タスクは揮発側に入る


def test_日報は当日の記録だけを材料にする(workspace, profile):
    workspace.add_note("本日の商談", "A社と面談")
    employee, client = make(workspace, profile, [say("日報を作成しました。")])
    result = employee.daily_report("2000-01-01")  # 記録のない日

    prompt = client.messages.calls[0]["messages"][-1]["content"]
    assert "## 本日の業務メモ (0 件)" in prompt
    assert "A社と面談" not in prompt
    assert "- なし" in prompt
    assert result.ok


def test_日報は当日の記録を差し込む(workspace, profile):
    from ai_employee.workspace import now

    today = now().strftime("%Y-%m-%d")
    workspace.add_note("本日の商談", "A社と面談")
    task = workspace.add_task("見積作成")
    workspace.close_task(task["id"], "送付済み")

    employee, client = make(workspace, profile, [say("作成しました。")])
    employee.daily_report()

    prompt = client.messages.calls[0]["messages"][-1]["content"]
    assert today in prompt
    assert "A社と面談" in prompt
    assert "送付済み" in prompt


def test_台本より多く呼ばれたら検知できる(workspace, profile):
    employee, _ = make(workspace, profile, [call_tool("current_datetime", {})])
    with pytest.raises(AssertionError):
        employee.work("やって")


def test_自分が主担当の進行中案件が毎回の判断材料に入る(workspace, profile):
    """案件は揮発側に置く。更新のたびにプロフィールのキャッシュを壊さないため。"""
    employee, client = make(workspace, profile, [say("はい")])
    project = employee.ledger.add("田中邸 新築", owner=profile.employee_id, by="shukyaku")
    employee.ledger.update(
        project["id"], "相談実施", stage="初回相談", next_action="現地調査の日程調整"
    )
    other = employee.ledger.add("他人の案件", owner="someone-else")

    employee.work("状況を教えて")
    stable, volatile = client.messages.calls[0]["system"]

    assert "田中邸 新築" in volatile["text"]
    assert "現地調査の日程調整" in volatile["text"]
    assert other["name"] not in volatile["text"]  # 他人の案件は載せない
    assert "田中邸 新築" not in stable["text"]


def test_案件ツールが社員に渡っている(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("やって")
    names = [t.get("name") for t in client.messages.calls[0]["tools"]]
    assert {"add_project", "list_projects", "get_project", "update_project"} <= set(names)


def test_社員は同じ案件台帳を共有する(workspace, profile, tmp_path):
    from ai_employee.workspace import Workspace

    other_ws = Workspace("bim", tmp_path)
    other_ws.save_profile(profile)
    a = Employee(profile, workspace, client=FakeClient([]))
    b = Employee(profile, other_ws, client=FakeClient([]))

    created = a.ledger.add("田中邸 新築", by="eigyo")
    assert b.ledger.get(created["id"])["name"] == "田中邸 新築"


def test_事務所プロフィールが不変ブロックに載る(workspace, profile, tmp_path):
    from ai_employee.company import OfficeProfile

    OfficeProfile(name="アペックス設計事務所", areas=["東京23区"]).save(tmp_path)
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("初回返信を作って")

    stable, volatile = client.messages.calls[0]["system"]
    assert "アペックス設計事務所" in stable["text"]
    assert "東京23区" in stable["text"]
    # 事務所情報は滅多に変わらないのでキャッシュされる側に置く
    assert stable["cache_control"] == {"type": "ephemeral"}
    assert "アペックス" not in volatile["text"]


def test_事務所プロフィール未設定なら作り話を禁じる指示が入る(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("初回返信を作って")
    stable = client.messages.calls[0]["system"][0]["text"]
    assert "未設定" in stable
    assert "書いてはいけません" in stable


def test_集客ツールが社員に渡っている(workspace, profile):
    employee, client = make(workspace, profile, [say("はい")])
    employee.work("追客漏れを教えて")
    names = [t.get("name") for t in client.messages.calls[0]["tools"]]
    assert {"stale_projects", "source_report"} <= set(names)
