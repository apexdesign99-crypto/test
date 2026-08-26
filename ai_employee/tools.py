"""AI社員が使える業務ツール。

各ツールは Claude に渡す JSON Schema と、実際に実行される Python 関数の組。
プロフィールの `tools` に列挙された名前だけが有効になる(権限管理)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .workspace import Workspace, WorkspaceError, now

# Opus 4.6 以降で使えるサーバ側 Web 検索ツール。
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}

# サーバ側で実行されるためクライアントに実装が不要なツール名。
SERVER_TOOL_NAMES = frozenset({"web_search"})


@dataclass(frozen=True)
class Tool:
    """1 つの業務ツール。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_tools(workspace: Workspace) -> dict[str, Tool]:
    """ワークスペースに紐づいた全ツールを構築する。"""

    def current_datetime() -> dict[str, Any]:
        stamp = now()
        weekdays = "月火水木金土日"
        return {
            "iso": stamp.isoformat(timespec="seconds"),
            "date": stamp.strftime("%Y-%m-%d"),
            "time": stamp.strftime("%H:%M"),
            "weekday": f"{weekdays[stamp.weekday()]}曜日",
        }

    def record_note(title: str, body: str, tags: list[str] | None = None) -> dict:
        return workspace.add_note(title, body, tags)

    def search_notes(
        query: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 10,
    ) -> dict:
        hits = workspace.search_notes(query=query, tag=tag, since=since, limit=limit)
        return {"count": len(hits), "notes": hits}

    def add_task(title: str, detail: str = "", due: str | None = None) -> dict:
        return workspace.add_task(title, detail, due)

    def list_tasks(status: str = "open") -> dict:
        tasks = workspace.list_tasks(status)
        return {"count": len(tasks), "tasks": tasks}

    def complete_task(task_id: str, result: str = "", cancelled: bool = False) -> dict:
        return workspace.close_task(
            task_id, result, status="cancelled" if cancelled else "done"
        )

    def list_files(subdir: str = "") -> dict:
        files = workspace.list_files(subdir)
        return {"count": len(files), "files": files}

    def read_file(path: str) -> dict:
        return {"path": path, "content": workspace.read_file(path)}

    def write_file(path: str, content: str) -> dict:
        saved = workspace.write_file(path, content)
        return {"path": path, "bytes": len(content.encode("utf-8")), "saved_to": str(saved)}

    tools = [
        Tool(
            "current_datetime",
            "現在の日付・時刻・曜日を取得する。日付に依存する判断の前に必ず呼ぶこと。",
            _obj({}, []),
            current_datetime,
        ),
        Tool(
            "record_note",
            "業務メモを記録する。商談・調査・対応の結果など、後から参照すべき事実を残す。",
            _obj(
                {
                    "title": {"type": "string", "description": "メモの表題(簡潔に)"},
                    "body": {
                        "type": "string",
                        "description": "本文。事実と、判断の根拠を書く。",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "検索用のタグ(顧客名・案件名など)",
                    },
                },
                ["title", "body"],
            ),
            record_note,
        ),
        Tool(
            "search_notes",
            "過去の業務メモを検索する。新しい順に返る。過去の経緯を確認したいときに使う。",
            _obj(
                {
                    "query": {"type": "string", "description": "表題・本文の部分一致"},
                    "tag": {"type": "string", "description": "タグの完全一致"},
                    "since": {
                        "type": "string",
                        "description": "この日時以降のみ (例 2026-04-01)",
                    },
                    "limit": {"type": "integer", "description": "最大件数(既定 10)"},
                },
                [],
            ),
            search_notes,
        ),
        Tool(
            "add_task",
            "自分のタスクを登録する。依頼を受けた作業や、後続で必要になった作業を残す。",
            _obj(
                {
                    "title": {"type": "string", "description": "やること"},
                    "detail": {"type": "string", "description": "背景や完了条件"},
                    "due": {"type": "string", "description": "期限 (例 2026-04-30)"},
                },
                ["title"],
            ),
            add_task,
        ),
        Tool(
            "list_tasks",
            "自分のタスク一覧を取得する。status は open / done / cancelled / all。",
            _obj(
                {
                    "status": {
                        "type": "string",
                        "enum": ["open", "done", "cancelled", "all"],
                        "description": "既定は open(未完了のみ)",
                    }
                },
                [],
            ),
            list_tasks,
        ),
        Tool(
            "complete_task",
            "タスクを完了(または中止)にする。result に何をしたかを書く。",
            _obj(
                {
                    "task_id": {"type": "string", "description": "対象タスクの ID"},
                    "result": {"type": "string", "description": "実施結果"},
                    "cancelled": {
                        "type": "boolean",
                        "description": "true なら中止として閉じる",
                    },
                },
                ["task_id"],
            ),
            complete_task,
        ),
        Tool(
            "list_files",
            "ワークスペースに保存されている成果物ファイルの一覧を取得する。",
            _obj({"subdir": {"type": "string", "description": "対象サブフォルダ"}}, []),
            list_files,
        ),
        Tool(
            "read_file",
            "ワークスペース内のファイルを読む。上書き前には必ずこれで内容を確認する。",
            _obj({"path": {"type": "string", "description": "files/ からの相対パス"}}, ["path"]),
            read_file,
        ),
        Tool(
            "write_file",
            "成果物をワークスペースに保存する。同名ファイルは上書きされる。",
            _obj(
                {
                    "path": {
                        "type": "string",
                        "description": "files/ からの相対パス (例 reports/2026-04.md)",
                    },
                    "content": {"type": "string", "description": "ファイル全文"},
                },
                ["path", "content"],
            ),
            write_file,
        ),
    ]
    return {tool.name: tool for tool in tools}


class ToolBox:
    """プロフィールの権限に従ってツールを絞り込み、実行を仲介する。"""

    def __init__(self, workspace: Workspace, allowed: list[str], web_access: bool = False):
        available = build_tools(workspace)
        unknown = [name for name in allowed if name not in available]
        if unknown:
            raise ValueError(f"未知のツールが指定されています: {unknown}")
        # 権限順ではなく定義順に固定する(プロンプトキャッシュの安定のため)。
        self.tools = {
            name: tool for name, tool in available.items() if name in set(allowed)
        }
        self.web_access = web_access

    def specs(self) -> list[dict[str, Any]]:
        """API に渡す tools 配列。"""
        specs: list[dict[str, Any]] = [t.spec() for t in self.tools.values()]
        if self.web_access:
            specs.append(dict(WEB_SEARCH_TOOL))
        return specs

    def run(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """ツールを実行し、(表示用テキスト, エラーか) を返す。"""
        tool = self.tools.get(name)
        if tool is None:
            return f"ツール '{name}' は利用権限がありません。", True
        try:
            result = tool.handler(**arguments)
        except WorkspaceError as exc:
            return f"エラー: {exc}", True
        except TypeError as exc:
            return f"エラー: 引数が不正です ({exc})", True
        except Exception as exc:  # noqa: BLE001 - 社員は落ちずに報告する
            return f"エラー: {type(exc).__name__}: {exc}", True
        if isinstance(result, str):
            return result, False
        return json.dumps(result, ensure_ascii=False, default=str), False
