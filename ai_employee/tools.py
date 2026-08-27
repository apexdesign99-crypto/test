"""AI社員が使える業務ツール。

各ツールは Claude に渡す JSON Schema と、実際に実行される Python 関数の組。
プロフィールの `tools` に列挙された名前だけが有効になる(権限管理)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .company import (
    HEARING_ITEMS,
    HEARING_KEYS,
    KINDS,
    STAGES,
    STATUSES,
    CompanyError,
    OfficeProfile,
    ProjectLedger,
)
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


def build_tools(
    workspace: Workspace, ledger: ProjectLedger | None = None
) -> dict[str, Tool]:
    """ワークスペースと案件台帳に紐づいた全ツールを構築する。"""
    ledger = ledger or ProjectLedger(workspace.root.parent)
    office = OfficeProfile.load(workspace.root.parent)
    me = workspace.employee_id

    def current_datetime() -> dict[str, Any]:
        stamp = now()
        weekdays = "月火水木金土日"
        return {
            "iso": stamp.isoformat(timespec="seconds"),
            "date": stamp.strftime("%Y-%m-%d"),
            "time": stamp.strftime("%H:%M"),
            "weekday": f"{weekdays[stamp.weekday()]}曜日",
        }

    def record_note(
        title: str,
        body: str,
        tags: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict:
        if project_id:
            ledger.get(project_id)  # 存在しない案件への紐付けを防ぐ
        return workspace.add_note(title, body, tags, project_id)

    def search_notes(
        query: str | None = None,
        tag: str | None = None,
        since: str | None = None,
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict:
        hits = workspace.search_notes(
            query=query, tag=tag, since=since, limit=limit, project_id=project_id
        )
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

    def add_project(
        name: str,
        client: str = "",
        kind: str = "その他",
        stage: str = "反響",
        source: str = "",
        site: str = "",
        budget: str = "",
        owner: str = "",
    ) -> dict:
        return ledger.add(
            name=name,
            client=client,
            kind=kind,
            stage=stage,
            source=source,
            site=site,
            budget=budget,
            owner=owner or me,
            by=me,
        )

    def list_projects(
        stage: str | None = None,
        status: str = "active",
        owner: str | None = None,
        query: str | None = None,
    ) -> dict:
        hits = ledger.list(stage=stage, status=status, owner=owner, query=query)
        # 一覧では履歴を落とす(全文は get_project で取る)。
        slim = [{k: v for k, v in p.items() if k != "history"} for p in hits]
        return {"count": len(slim), "projects": slim}

    def get_project(project_id: str) -> dict:
        return ledger.get(project_id)

    def update_project(
        project_id: str,
        note: str,
        stage: str | None = None,
        status: str | None = None,
        next_action: str | None = None,
        next_due: str | None = None,
        budget: str | None = None,
        owner: str | None = None,
        client: str | None = None,
        site: str | None = None,
    ) -> dict:
        return ledger.update(
            project_id,
            note=note,
            by=me,
            stage=stage,
            status=status,
            next_action=next_action,
            next_due=next_due,
            budget=budget,
            owner=owner,
            client=client,
            site=site,
        )

    def log_project(project_id: str, entry: str) -> dict:
        updated = ledger.log(project_id, entry, by=me)
        return {"id": updated["id"], "history": updated["history"][-1]}

    def pipeline() -> dict:
        counts = ledger.pipeline()
        return {"active_total": sum(counts.values()), "by_stage": counts}

    def stale_projects(days: int = 14, stage: str | None = None) -> dict:
        stalled = ledger.stale(days=days, stage=stage)
        slim = [
            {
                "id": p["id"],
                "name": p["name"],
                "client": p["client"],
                "stage": p["stage"],
                "owner": p["owner"],
                "source": p["source"],
                "next_action": p["next_action"],
                "next_due": p["next_due"],
                "updated_at": p["updated_at"],
                "last_entry": p["history"][-1]["entry"] if p["history"] else "",
            }
            for p in stalled
        ]
        return {"threshold_days": days, "count": len(slim), "projects": slim}

    def source_report(since: str | None = None) -> dict:
        report = ledger.by_source(since=since)
        return {"since": since, "sources": report}

    def record_hearing(project_id: str, **items: str) -> dict:
        updated = ledger.record_hearing(project_id, items, by=me)
        return {
            "id": updated["id"],
            "requirements": updated["requirements"],
            "gaps": ledger.hearing_gaps(project_id),
        }

    def hearing_gaps(project_id: str) -> dict:
        return ledger.hearing_gaps(project_id)

    def estimate_cost(
        kind: str,
        floor_area_tsubo: float | None = None,
        floor_area_sqm: float | None = None,
    ) -> dict:
        return office.estimate(
            kind, floor_area_tsubo=floor_area_tsubo, floor_area_sqm=floor_area_sqm
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
                        "description": "検索用のタグ(顧客名・工種など)",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "紐付ける案件の ID。案件に関する記録なら必ず指定する。",
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
                    "project_id": {
                        "type": "string",
                        "description": "この案件に紐づくメモだけに絞る",
                    },
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
            "add_project",
            "案件台帳に新しい案件を登録する。反響・問い合わせが入った時点で必ず起こすこと。"
            "既存案件の重複登録を避けるため、先に list_projects で確認する。",
            _obj(
                {
                    "name": {"type": "string", "description": "案件名 (例: 田中邸 新築)"},
                    "client": {"type": "string", "description": "施主・顧客名"},
                    "kind": {"type": "string", "enum": list(KINDS), "description": "用途種別"},
                    "stage": {"type": "string", "enum": list(STAGES), "description": "現在のステージ(既定 反響)"},
                    "source": {"type": "string", "description": "流入経路 (例: HP問い合わせ, 紹介, Instagram)"},
                    "site": {"type": "string", "description": "計画地"},
                    "budget": {"type": "string", "description": "予算(聞けている範囲で。推測しない)"},
                    "owner": {"type": "string", "description": "主担当の社員 ID(既定は自分)"},
                },
                ["name"],
            ),
            add_project,
        ),
        Tool(
            "list_projects",
            "案件台帳を検索する。既定では進行中案件のみ、次アクションの期限が近い順に返る。"
            "案件の話をする前にまずこれで現状を確認すること。",
            _obj(
                {
                    "stage": {"type": "string", "enum": list(STAGES), "description": "このステージのみ"},
                    "status": {
                        "type": "string",
                        "enum": [*STATUSES, "all"],
                        "description": "active(進行中) / won(受注) / lost(失注) / onhold(保留) / done(完了) / all",
                    },
                    "owner": {"type": "string", "description": "主担当の社員 ID"},
                    "query": {"type": "string", "description": "案件名・顧客名・計画地の部分一致"},
                },
                [],
            ),
            list_projects,
        ),
        Tool(
            "get_project",
            "案件 1 件の全項目と、これまでの経緯(履歴)を取得する。"
            "誰が何をしたかを確認してから動くこと。",
            _obj({"project_id": {"type": "string", "description": "案件 ID"}}, ["project_id"]),
            get_project,
        ),
        Tool(
            "update_project",
            "案件の状態を更新する。ステージが進んだとき、次アクションが決まったとき、"
            "受注・失注が確定したときに使う。note に「何をなぜ変えたか」を必ず書く。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "note": {"type": "string", "description": "更新理由(履歴に残る。必須)"},
                    "stage": {"type": "string", "enum": list(STAGES), "description": "新しいステージ"},
                    "status": {"type": "string", "enum": list(STATUSES), "description": "新しいステータス"},
                    "next_action": {"type": "string", "description": "次にやること"},
                    "next_due": {"type": "string", "description": "次アクションの期限 (例 2026-09-30)"},
                    "budget": {"type": "string", "description": "予算"},
                    "owner": {"type": "string", "description": "主担当の社員 ID"},
                    "client": {"type": "string", "description": "施主・顧客名"},
                    "site": {"type": "string", "description": "計画地"},
                },
                ["project_id", "note"],
            ),
            update_project,
        ),
        Tool(
            "log_project",
            "案件の履歴に出来事を 1 行追記する。項目は変えずに経緯だけ残したいときに使う。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "entry": {"type": "string", "description": "起きた事実"},
                },
                ["project_id", "entry"],
            ),
            log_project,
        ),
        Tool(
            "pipeline",
            "進行中案件のステージ別件数を取得する。営業会議や受注見込みの把握に使う。",
            _obj({}, []),
            pipeline,
        ),
        Tool(
            "stale_projects",
            "一定期間動いていない進行中案件を、放置が長い順に返す。追客漏れの検知に使う。"
            "報告する前に必ずこれで取りこぼしを確認すること。",
            _obj(
                {
                    "days": {
                        "type": "integer",
                        "description": "最終更新から何日以上動いていないものを対象にするか(既定 14)",
                    },
                    "stage": {
                        "type": "string",
                        "enum": list(STAGES),
                        "description": "このステージに限定する",
                    },
                },
                [],
            ),
            stale_projects,
        ),
        Tool(
            "source_report",
            "流入経路ごとの反響数・受注・失注・受注率を集計する。"
            "どの集客施策が効いているかを判断する材料。受注率は決着済み案件に対する割合で、"
            "進行中は母数に含まれない。",
            _obj(
                {
                    "since": {
                        "type": "string",
                        "description": "この日以降に起票された案件のみ (例 2026-04-01)",
                    }
                },
                [],
            ),
            source_report,
        ),
        Tool(
            "record_hearing",
            "初回相談などで聞けた内容を案件に記録する。聞けた項目だけ渡せばよい。"
            "渡さなかった項目は未確認のまま残り、hearing_gaps で拾える。"
            "推測で埋めてはいけない。聞けていない項目は渡さないこと。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    **{
                        key: {"type": "string", "description": f"{label} — 聞けた内容をそのまま"}
                        for key, label, _ in HEARING_ITEMS
                    },
                },
                ["project_id"],
            ),
            record_hearing,
        ),
        Tool(
            "hearing_gaps",
            "案件のヒアリング状況を返す。聞けた内容、未確認の項目、"
            "提案に進んでよいか(必須項目が埋まっているか)が分かる。"
            "プラン提案・見積の話をする前に必ず確認すること。",
            _obj({"project_id": {"type": "string", "description": "案件 ID"}}, ["project_id"]),
            hearing_gaps,
        ),
        Tool(
            "estimate_cost",
            "延床面積から工事費と設計監理料の概算レンジを算定する。"
            "金額を出すときは必ずこれを使い、自分で掛け算をしないこと。"
            "事務所に坪単価が未設定の用途では失敗する。その場合は概算金額を書かず、"
            "算定できない旨を報告すること。",
            _obj(
                {
                    "kind": {"type": "string", "enum": list(KINDS), "description": "用途種別"},
                    "floor_area_tsubo": {"type": "number", "description": "延床面積(坪)"},
                    "floor_area_sqm": {"type": "number", "description": "延床面積(㎡)"},
                },
                ["kind"],
            ),
            estimate_cost,
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

    def __init__(
        self,
        workspace: Workspace,
        allowed: list[str],
        web_access: bool = False,
        ledger: ProjectLedger | None = None,
    ):
        available = build_tools(workspace, ledger)
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
        except (WorkspaceError, CompanyError) as exc:
            return f"エラー: {exc}", True
        except TypeError as exc:
            return f"エラー: 引数が不正です ({exc})", True
        except Exception as exc:  # noqa: BLE001 - 社員は落ちずに報告する
            return f"エラー: {type(exc).__name__}: {exc}", True
        if isinstance(result, str):
            return result, False
        return json.dumps(result, ensure_ascii=False, default=str), False
