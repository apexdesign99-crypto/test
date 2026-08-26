"""AI社員を採用し、働かせるためのコマンドライン。

    python -m ai_employee hire   --id sato --name "佐藤 AI" --template sales
    python -m ai_employee roster
    python -m ai_employee ask    --id sato "A社の商談メモをまとめて"
    python -m ai_employee chat   --id sato
    python -m ai_employee report --id sato
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .agent import Employee, Listener
from .config import office_root
from .profile import TEMPLATES, EmployeeProfile, build_profile, slugify
from .workspace import Workspace, WorkspaceError, roster

# ANSI 色。パイプ出力時は無効化する。
_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


DIM = lambda s: _c("2", s)  # noqa: E731
BOLD = lambda s: _c("1", s)  # noqa: E731
CYAN = lambda s: _c("36", s)  # noqa: E731
RED = lambda s: _c("31", s)  # noqa: E731


class ConsoleListener(Listener):
    """ストリーミング中の進捗を端末に流す。"""

    def __init__(self, show_thinking: bool = False) -> None:
        self.show_thinking = show_thinking
        self._in_thinking = False

    def on_thinking(self, text: str) -> None:
        if not self.show_thinking:
            return
        if not self._in_thinking:
            print(DIM("\n[思考] "), end="")
            self._in_thinking = True
        print(DIM(text), end="", flush=True)

    def on_text(self, text: str) -> None:
        if self._in_thinking:
            print()
            self._in_thinking = False
        print(text, end="", flush=True)

    def on_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        if self._in_thinking:
            print()
            self._in_thinking = False
        preview = json.dumps(arguments, ensure_ascii=False)
        if len(preview) > 120:
            preview = preview[:117] + "..."
        print(CYAN(f"\n  ▸ {name} {preview}"), flush=True)

    def on_tool_result(self, name: str, output: str, is_error: bool) -> None:
        head = output.splitlines()[0] if output else ""
        if len(head) > 120:
            head = head[:117] + "..."
        mark = RED("  ✗ ") if is_error else DIM("  ✓ ")
        print(mark + (RED(head) if is_error else DIM(head)), flush=True)

    def on_notice(self, message: str) -> None:
        print(RED(f"\n[通知] {message}"), flush=True)


def _workspace(employee_id: str, root: Path | None) -> Workspace:
    return Workspace(employee_id, root)


def _employee(args: argparse.Namespace) -> Employee:
    ws = _workspace(args.id, args.office)
    profile = ws.load_profile()
    ws.ensure()
    return Employee(
        profile, ws, listener=ConsoleListener(show_thinking=getattr(args, "thinking", False))
    )


# ------------------------------------------------------------------ コマンド


def cmd_hire(args: argparse.Namespace) -> int:
    employee_id = args.id or slugify(args.name)
    ws = _workspace(employee_id, args.office)
    if ws.exists() and not args.force:
        print(RED(f"社員 '{employee_id}' は既に在籍しています (--force で上書き)"))
        return 1
    profile = build_profile(
        employee_id,
        args.name,
        template=args.template,
        role=args.role,
        department=args.department,
        mission=args.mission,
        web_access=True if args.web else None,
    )
    ws.save_profile(profile)
    print(BOLD(f"{profile.name} を {profile.department} の {profile.role} として採用しました。"))
    print(DIM(f"  ID          : {profile.employee_id}"))
    print(DIM(f"  職務定義書  : {ws.profile_path}"))
    print(DIM(f"  ワークスペース: {ws.root}"))
    print(DIM(f"  権限        : {', '.join(profile.tools)}"))
    if profile.web_access:
        print(DIM("  Web 検索    : 有効"))
    print()
    print(f"次: python -m ai_employee ask --id {employee_id} \"最初の依頼\"")
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    people = roster(args.office)
    if not people:
        print(f"在籍者はいません ({args.office or office_root()})。`hire` で採用してください。")
        return 0
    print(BOLD(f"在籍者 {len(people)} 名"))
    for p in people:
        ws = _workspace(p.employee_id, args.office)
        open_tasks = len(ws.list_tasks("open"))
        print(f"  {p.employee_id:<14} {p.name}  {p.department}/{p.role}  未完了 {open_tasks} 件")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    employee = _employee(args)
    history = employee.workspace.load_session() if args.remember else []
    result = employee.work(args.instruction, history)
    print()
    if args.remember:
        employee.workspace.save_session(result.messages)
    if result.refusal:
        return 2
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    employee = _employee(args)
    ws = employee.workspace
    history = ws.load_session()
    print(BOLD(f"{employee.profile.name}({employee.profile.role})と接続しました。"))
    print(DIM("終了は /exit、履歴のクリアは /clear。"))
    if history:
        print(DIM(f"本日分の会話 {len(history)} 件を引き継ぎました。"))
    while True:
        try:
            line = input(BOLD("\nあなた > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/clear":
            history = []
            ws.save_session(history)
            print(DIM("履歴をクリアしました。"))
            continue
        print(BOLD(f"\n{employee.profile.name} > "), end="", flush=True)
        result = employee.work(line, history)
        history = result.messages
        ws.save_session(history)
        print()
    print(DIM("お疲れさまでした。"))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    employee = _employee(args)
    result = employee.daily_report(args.date)
    print()
    return 2 if result.refusal else 0


def cmd_tasks(args: argparse.Namespace) -> int:
    ws = _workspace(args.id, args.office)
    ws.load_profile()
    tasks = ws.list_tasks(args.status)
    if not tasks:
        print("該当するタスクはありません。")
        return 0
    for t in tasks:
        due = f" 期限 {t['due']}" if t.get("due") else ""
        print(f"[{t['id']}] {t['status']:<9} {t['title']}{due}")
        if t.get("result"):
            print(DIM(f"           → {t['result']}"))
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    ws = _workspace(args.id, args.office)
    ws.load_profile()
    notes = ws.search_notes(query=args.query, tag=args.tag, limit=args.limit)
    if not notes:
        print("該当するメモはありません。")
        return 0
    for n in notes:
        tags = f"  [{', '.join(n['tags'])}]" if n["tags"] else ""
        print(BOLD(f"{n['created_at']}  {n['title']}") + DIM(tags))
        print(f"  {n['body']}")
    return 0


def cmd_templates(_: argparse.Namespace) -> int:
    print(BOLD("利用できる職種テンプレート"))
    for key, data in TEMPLATES.items():
        print(f"  {key:<12} {data['department']}/{data['role']} — {data['mission']}")
    return 0


# ------------------------------------------------------------------ パーサ


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_employee",
        description="AI社員を採用し、業務を任せるためのツール",
    )
    parser.add_argument(
        "--office",
        type=Path,
        default=None,
        help=f"社員データの置き場 (既定: {office_root()})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_id(p: argparse.ArgumentParser) -> None:
        p.add_argument("--id", required=True, help="社員 ID")

    def add_thinking(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--thinking", action="store_true", help="思考の要約も表示する"
        )

    p_hire = sub.add_parser("hire", help="AI社員を採用する")
    p_hire.add_argument("--name", required=True, help="氏名")
    p_hire.add_argument("--id", help="社員 ID (省略時は氏名から生成)")
    p_hire.add_argument(
        "--template", default="assistant", choices=sorted(TEMPLATES), help="職種"
    )
    p_hire.add_argument("--role", help="役職を上書き")
    p_hire.add_argument("--department", help="所属を上書き")
    p_hire.add_argument("--mission", help="ミッションを上書き")
    p_hire.add_argument("--web", action="store_true", help="Web 検索の権限を付与")
    p_hire.add_argument("--force", action="store_true", help="既存社員を上書き")
    p_hire.set_defaults(func=cmd_hire)

    p_roster = sub.add_parser("roster", help="在籍者を一覧する")
    p_roster.set_defaults(func=cmd_roster)

    p_ask = sub.add_parser("ask", help="単発で業務を依頼する")
    add_id(p_ask)
    add_thinking(p_ask)
    p_ask.add_argument("instruction", help="依頼内容")
    p_ask.add_argument(
        "--remember", action="store_true", help="本日分の会話履歴に引き継ぐ"
    )
    p_ask.set_defaults(func=cmd_ask)

    p_chat = sub.add_parser("chat", help="対話しながら業務を進める")
    add_id(p_chat)
    add_thinking(p_chat)
    p_chat.set_defaults(func=cmd_chat)

    p_report = sub.add_parser("report", help="日報を書かせる")
    add_id(p_report)
    add_thinking(p_report)
    p_report.add_argument("--date", help="対象日 (既定: 本日)")
    p_report.set_defaults(func=cmd_report)

    p_tasks = sub.add_parser("tasks", help="タスク一覧を見る")
    add_id(p_tasks)
    p_tasks.add_argument(
        "--status", default="open", choices=["open", "done", "cancelled", "all"]
    )
    p_tasks.set_defaults(func=cmd_tasks)

    p_notes = sub.add_parser("notes", help="業務メモを検索する")
    add_id(p_notes)
    p_notes.add_argument("--query", help="本文・表題の部分一致")
    p_notes.add_argument("--tag", help="タグ")
    p_notes.add_argument("--limit", type=int, default=10)
    p_notes.set_defaults(func=cmd_notes)

    p_tpl = sub.add_parser("templates", help="職種テンプレートを一覧する")
    p_tpl.set_defaults(func=cmd_templates)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
