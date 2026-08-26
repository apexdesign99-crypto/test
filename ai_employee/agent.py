"""AI社員の実行エンジン(エージェントループ)。

Claude Opus 5 にストリーミングで指示を渡し、ツール呼び出しを実行して返す、
という往復を「その社員が仕事を終えたと判断するまで」繰り返す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import (
    FALLBACK_BETA,
    MAX_PAUSE_RESTARTS,
    MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
)
from .company import ProjectLedger
from .profile import EmployeeProfile
from .tools import ToolBox
from .workspace import Workspace, now


class Listener:
    """進捗を受け取るためのフック。CLI は継承して画面表示に使う。"""

    def on_thinking(self, text: str) -> None: ...

    def on_text(self, text: str) -> None: ...

    def on_tool_call(self, name: str, arguments: dict[str, Any]) -> None: ...

    def on_tool_result(self, name: str, output: str, is_error: bool) -> None: ...

    def on_notice(self, message: str) -> None: ...


@dataclass
class TurnResult:
    """1 回の依頼に対する仕事の結果。"""

    text: str
    messages: list[dict[str, Any]]
    stop_reason: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    refusal: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.refusal is None and not self.truncated


def _dump(block: Any) -> dict[str, Any]:
    """レスポンスのコンテンツブロックを JSON 化可能な dict にする。"""
    if isinstance(block, dict):
        return block
    dump = getattr(block, "model_dump", None)
    if dump is not None:
        return dump()
    return dict(block)  # pragma: no cover - SDK 変更時の保険


def _text_of(content: Iterable[Any]) -> str:
    parts = []
    for block in content:
        data = _dump(block)
        if data.get("type") == "text":
            parts.append(data.get("text", ""))
    return "".join(parts).strip()


class Employee:
    """職務定義書に従って働く AI社員。"""

    def __init__(
        self,
        profile: EmployeeProfile,
        workspace: Workspace,
        client: Any = None,
        listener: Listener | None = None,
        ledger: ProjectLedger | None = None,
    ) -> None:
        self.profile = profile
        self.workspace = workspace
        self.listener = listener or Listener()
        # 案件台帳は事務所で 1 つ。社員のワークスペースの親(= 会社)に置く。
        self.ledger = ledger or ProjectLedger(workspace.root.parent)
        self.toolbox = ToolBox(
            workspace, profile.tools, profile.web_access, ledger=self.ledger
        )
        self._client = client

    # ---------------------------------------------------------------- クライアント

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic  # 遅延 import: API を使わない操作では不要

            self._client = anthropic.Anthropic()
        return self._client

    # -------------------------------------------------------------- プロンプト

    def system_blocks(self) -> list[dict[str, Any]]:
        """system プロンプトを「不変部分 → 揮発部分」の順で組み立てる。

        不変部分の末尾にキャッシュ区切りを置くことで、日付や未完了タスクが
        変わってもプロフィール部分のキャッシュは効き続ける。
        """
        stamp = now()
        open_tasks = self.workspace.list_tasks("open")
        volatile = [
            "# 現在の勤務状況",
            f"- 現在時刻: {stamp.isoformat(timespec='minutes')}",
            f"- 未完了タスク: {len(open_tasks)} 件",
        ]
        for task in open_tasks[:20]:
            due = f" / 期限 {task['due']}" if task.get("due") else ""
            volatile.append(f"  - [{task['id']}] {task['title']}{due}")
        if len(open_tasks) > 20:
            volatile.append(f"  - ...ほか {len(open_tasks) - 20} 件")

        # 自分が主担当の進行中案件は、毎回の判断材料になるので常に渡す。
        mine = self.ledger.list(owner=self.profile.employee_id)
        volatile.append(f"- 自分が主担当の進行中案件: {len(mine)} 件")
        for project in mine[:15]:
            due = f" / 期限 {project['next_due']}" if project.get("next_due") else ""
            action = project.get("next_action") or "次アクション未設定"
            volatile.append(
                f"  - [{project['id']}] {project['name']} ({project['stage']}) "
                f"→ {action}{due}"
            )
        if len(mine) > 15:
            volatile.append(f"  - ...ほか {len(mine) - 15} 件")

        return [
            {
                "type": "text",
                "text": self.profile.system_prompt(),
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "\n".join(volatile)},
        ]

    def _request_kwargs(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "model": self.profile.model,
            "max_tokens": MAX_TOKENS,
            "system": self.system_blocks(),
            # 送信時点のスナップショットを渡す。以降のループでの追記が
            # 送信済みリクエストに遡って影響しないようにするため。
            "messages": list(messages),
            "tools": self.toolbox.specs(),
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.profile.effort},
            # Opus 5 は安全性判定で応答を拒否することがあるため、
            # サーバ側フォールバックを既定で有効にしておく。
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    # ------------------------------------------------------------------ 実行

    def work(
        self,
        instruction: str,
        history: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        """指示を 1 件受け取り、完了するまで働いて結果を返す。"""
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": instruction})

        tool_calls: list[str] = []
        pause_restarts = 0
        last_content: list[dict[str, Any]] = []
        stop_reason: str | None = None

        for _ in range(MAX_TOOL_ITERATIONS):
            message = self._stream_once(messages)
            stop_reason = getattr(message, "stop_reason", None)
            last_content = [_dump(b) for b in message.content]
            messages.append({"role": "assistant", "content": last_content})

            if stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                reason = getattr(details, "explanation", None) or "理由の説明はありません"
                self.listener.on_notice(f"応答が拒否されました: {reason}")
                return TurnResult(
                    text=_text_of(last_content),
                    messages=messages,
                    stop_reason=stop_reason,
                    tool_calls=tool_calls,
                    refusal=str(reason),
                )

            if stop_reason == "pause_turn":
                pause_restarts += 1
                if pause_restarts > MAX_PAUSE_RESTARTS:
                    self.listener.on_notice("サーバツールの中断が続いたため打ち切りました。")
                    break
                continue

            if stop_reason == "max_tokens":
                self.listener.on_notice("出力上限に達したため途中で終了しました。")
                return TurnResult(
                    text=_text_of(last_content),
                    messages=messages,
                    stop_reason=stop_reason,
                    tool_calls=tool_calls,
                    truncated=True,
                )

            pending = [b for b in last_content if b.get("type") == "tool_use"]
            if not pending:
                break

            results = []
            for block in pending:
                name = block.get("name", "")
                arguments = block.get("input") or {}
                tool_calls.append(name)
                self.listener.on_tool_call(name, arguments)
                output, is_error = self.toolbox.run(name, arguments)
                self.listener.on_tool_result(name, output, is_error)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.get("id"),
                        "content": output,
                        "is_error": is_error,
                    }
                )
            # 並列ツール呼び出しの結果は 1 つの user メッセージにまとめて返す。
            messages.append({"role": "user", "content": results})
        else:
            self.listener.on_notice(
                f"ツール呼び出しが上限({MAX_TOOL_ITERATIONS} 回)に達したため終了しました。"
            )

        return TurnResult(
            text=_text_of(last_content),
            messages=messages,
            stop_reason=stop_reason,
            tool_calls=tool_calls,
        )

    def _stream_once(self, messages: list[dict[str, Any]]) -> Any:
        """1 往復ぶんをストリーミングで受け取り、確定メッセージを返す。"""
        kwargs = self._request_kwargs(messages)
        with self.client.beta.messages.stream(**kwargs) as stream:
            for event in stream:
                kind = getattr(event, "type", None)
                if kind != "content_block_delta":
                    continue
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)
                if delta_type == "text_delta":
                    self.listener.on_text(getattr(delta, "text", ""))
                elif delta_type == "thinking_delta":
                    self.listener.on_thinking(getattr(delta, "thinking", ""))
            return stream.get_final_message()

    # -------------------------------------------------------------- 定型業務

    def daily_report(self, date: str | None = None) -> TurnResult:
        """当日の業務メモとタスクから日報を書かせる。"""
        day = date or now().strftime("%Y-%m-%d")
        notes = [n for n in self.workspace.iter_notes() if n["created_at"].startswith(day)]
        tasks = self.workspace.list_tasks("all")
        closed = [t for t in tasks if (t.get("closed_at") or "").startswith(day)]
        open_tasks = [t for t in tasks if t["status"] == "open"]

        def section(header: str, rows: list[str]) -> list[str]:
            return [header, *(rows or ["- なし"]), ""]

        lines = [f"{day} の日報を作成してください。", ""]
        lines += section(
            f"## 本日の業務メモ ({len(notes)} 件)",
            [f"- [{n['created_at'][11:16]}] {n['title']}: {n['body']}" for n in notes],
        )
        lines += section(
            f"## 本日クローズしたタスク ({len(closed)} 件)",
            [
                f"- {t['title']} → {t['status']}: {t.get('result') or '結果の記載なし'}"
                for t in closed
            ],
        )
        lines += section(
            f"## 未完了タスク ({len(open_tasks)} 件)",
            [
                f"- [{t['id']}] {t['title']}"
                + (f" (期限 {t['due']})" if t.get("due") else "")
                for t in open_tasks
            ],
        )
        lines.append(
            "上記の事実だけを使って、「本日の実績」「所感・課題」「明日の予定」の"
            "3 部構成で日報をまとめ、write_file で reports/日報-"
            f"{day}.md に保存してください。記録にない出来事を創作しないこと。"
        )
        return self.work("\n".join(lines))
