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
import unicodedata
from pathlib import Path
from typing import Any

from .agent import Employee, Listener
from .config import office_root
from .company import STAGES, CompanyError, OfficeProfile, ProjectLedger
from .profile import DEFAULT_TEAM, TEMPLATES, EmployeeProfile, build_profile, slugify
from .workspace import Workspace, WorkspaceError, roster

# ANSI 色。パイプ出力時は無効化する。
_COLOR = sys.stdout.isatty()


def _width(text: str) -> int:
    """端末上の表示幅。全角文字を 2 桁として数える。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, gap: int = 1) -> str:
    """表示幅を揃えて右側を空白で埋める。

    幅を超える項目でも列がくっつかないよう、最低 `gap` 桁は必ず空ける。
    """
    return text + " " * max(gap, width - _width(text))


def _ralign(text: str, width: int) -> str:
    """表示幅を揃えて右寄せする。"""
    return " " * max(0, width - _width(text)) + text


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


def cmd_office(args: argparse.Namespace) -> int:
    """事務所プロフィールを作成・確認する。

    ここが未設定だと、社員は施主向けの文面に事務所固有の情報を書けない
    (作り話を防ぐため、意図的にそう指示している)。
    """
    office = OfficeProfile.load(args.office)

    if args.show:
        print(office.as_prompt())
        return 0

    changed = False
    for attr in ("name", "location", "fee_policy", "business_hours", "contact", "notes"):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(office, attr, value)
            changed = True
    for attr in ("areas", "specialties", "consultation_flow"):
        value = getattr(args, attr, None)
        if value is not None:
            setattr(office, attr, [v.strip() for v in value.split(",") if v.strip()])
            changed = True

    path = office.save(args.office)
    if not changed and not office.is_configured():
        print(BOLD("事務所プロフィールの雛形を作成しました。"))
        print(f"  {path}")
        print()
        print("このファイルを直接編集するか、次のように指定してください:")
        print(DIM(
            '  python -m ai_employee office --name "○○設計事務所" \\\n'
            '      --areas "東京23区,川崎市" --fee-policy "設計監理料は工事費の10%"'
        ))
        print()
        print(RED("未設定のあいだ、社員は施主向けの文面に"
                  "事務所名・エリア・料金・日程を書きません(作り話を防ぐため)。"))
        return 0

    print(BOLD("事務所プロフィールを保存しました。"))
    print(f"  {path}")
    print()
    print(office.as_prompt())
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    """追客が止まっている案件を洗い出す。"""
    stalled = ProjectLedger(args.office).stale(days=args.days, stage=args.stage)
    if not stalled:
        print(f"{args.days} 日以上動いていない進行中案件はありません。")
        return 0
    print(BOLD(f"{args.days} 日以上動いていない案件 {len(stalled)} 件") + DIM("(放置が長い順)"))
    for pj in stalled:
        print(
            BOLD(f"  [{pj['id']}] {pj['name']}")
            + f"  {pj['stage']}  最終更新 {pj['updated_at'][:10]}"
        )
        print(
            f"      担当: {pj.get('owner') or '-'}  経路: {pj.get('source') or '-'}  "
            f"次: {pj.get('next_action') or RED('未設定')}"
        )
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    """流入経路ごとの反響数と受注率を集計する。"""
    report = ProjectLedger(args.office).by_source(since=args.since)
    if not report:
        print("集計できる案件がありません。")
        return 0
    span = f"({args.since} 以降)" if args.since else "(全期間)"
    print(BOLD(f"流入経路別の反響 {span}"))
    header = (
        _pad("経路", 20)
        + _ralign("反響", 5)
        + _ralign("進行中", 7)
        + _ralign("受注", 5)
        + _ralign("失注", 5)
        + "  受注率"
    )
    print(DIM("  " + header))
    for row in report:
        rate = f"{row['win_rate']}%" if row["win_rate"] is not None else DIM("-")
        decided = row["won"] + row["lost"]
        note = DIM("  ※母数少") if 0 < decided < 5 else ""
        print(
            f"  {_pad(row['source'], 20)}{row['total']:>5}{row['active']:>7}"
            f"{row['won']:>5}{row['lost']:>5}  {rate}{note}"
        )
    print(DIM("\n  受注率は決着済み(受注+失注)に対する割合。進行中は母数に含まない。"))
    return 0


def cmd_hire_team(args: argparse.Namespace) -> int:
    """設計事務所の標準的な陣容(集客・営業・マーケ・事務・BIM)を一括採用する。"""
    hired, skipped = [], []
    for employee_id, name, template in DEFAULT_TEAM:
        ws = _workspace(employee_id, args.office)
        if ws.exists() and not args.force:
            skipped.append(employee_id)
            continue
        profile = build_profile(employee_id, name, template=template)
        ws.save_profile(profile)
        hired.append(profile)

    for profile in hired:
        print(
            f"  採用: {profile.employee_id:<10} {_pad(profile.name, 12)}"
            f"{profile.department}/{profile.role}"
        )
    if skipped:
        print(DIM(f"  既に在籍のため見送り: {', '.join(skipped)} (--force で上書き)"))
    if hired:
        print()
        print(BOLD(f"{len(hired)} 名を採用しました。"))
        print(f"次: python -m ai_employee ask --id shukyaku \"HP から問い合わせが入りました。…\"")
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    """案件台帳を人間が確認する。"""
    ledger = ProjectLedger(args.office)
    if args.pipeline:
        counts = ledger.pipeline()
        total = sum(counts.values())
        print(BOLD(f"進行中案件 {total} 件"))
        for stage in STAGES:
            if counts[stage]:
                bar = "█" * counts[stage]
                print(f"  {_pad(stage, 10)}{counts[stage]:>3}  {CYAN(bar)}")
        return 0

    projects = ledger.list(stage=args.stage, status=args.status, owner=args.owner, query=args.query)
    if not projects:
        print("該当する案件はありません。")
        return 0
    for pj in projects:
        due = f"  期限 {pj['next_due']}" if pj.get("next_due") else ""
        print(BOLD(f"[{pj['id']}] {pj['name']}") + f"  {pj['stage']}/{pj['status']}" + DIM(due))
        detail = "  ".join(
            filter(None, [pj.get("client"), pj.get("kind"), pj.get("site"), pj.get("source")])
        )
        if detail:
            print(DIM(f"    {detail}"))
        print(f"    次: {pj.get('next_action') or DIM('未設定')}  担当: {pj.get('owner') or '-'}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    """案件 1 件の詳細と、これまでの経緯を表示する。"""
    pj = ProjectLedger(args.office).get(args.project_id)
    print(BOLD(f"[{pj['id']}] {pj['name']}"))
    for label, key in [
        ("施主", "client"), ("用途", "kind"), ("計画地", "site"),
        ("流入経路", "source"), ("予算", "budget"), ("主担当", "owner"),
        ("ステージ", "stage"), ("ステータス", "status"),
        ("次アクション", "next_action"), ("期限", "next_due"),
    ]:
        print(f"  {_pad(label, 12, gap=0)}: {pj.get(key) or DIM('-')}")
    print(BOLD(f"\n  経緯 ({len(pj['history'])} 件)"))
    for entry in pj["history"]:
        print(f"    {entry['at'][:16]}  {entry['by']:<10} {entry['entry']}")
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
        print(
            f"  {p.employee_id:<10} {_pad(p.name, 12)}"
            f"{_pad(p.department + '/' + p.role, 26, gap=2)}未完了 {open_tasks} 件"
        )
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

    p_office = sub.add_parser(
        "office", help="事務所プロフィールを設定する(施主向け文面の前提になる)"
    )
    p_office.add_argument("--show", action="store_true", help="現在の設定を表示する")
    p_office.add_argument("--name", help="事務所名")
    p_office.add_argument("--location", help="所在地")
    p_office.add_argument("--areas", help="対応エリア(カンマ区切り)")
    p_office.add_argument("--specialties", help="得意分野(カンマ区切り)")
    p_office.add_argument("--fee-policy", dest="fee_policy", help="料金の考え方")
    p_office.add_argument(
        "--consultation-flow", dest="consultation_flow", help="初回相談の流れ(カンマ区切り)"
    )
    p_office.add_argument("--business-hours", dest="business_hours", help="営業時間")
    p_office.add_argument("--contact", help="連絡先")
    p_office.add_argument("--notes", help="補足")
    p_office.set_defaults(func=cmd_office)

    p_stale = sub.add_parser("stale", help="追客が止まっている案件を洗い出す")
    p_stale.add_argument("--days", type=int, default=14, help="何日以上動いていないか(既定 14)")
    p_stale.add_argument("--stage", choices=list(STAGES), help="このステージのみ")
    p_stale.set_defaults(func=cmd_stale)

    p_sources = sub.add_parser("sources", help="流入経路別の反響数と受注率を集計する")
    p_sources.add_argument("--since", help="この日以降に起票された案件のみ (例 2026-04-01)")
    p_sources.set_defaults(func=cmd_sources)

    p_team = sub.add_parser(
        "hire-team", help="設計事務所の標準陣容(集客・営業・マーケ・事務・BIM)を一括採用する"
    )
    p_team.add_argument("--force", action="store_true", help="既存社員を上書き")
    p_team.set_defaults(func=cmd_hire_team)

    p_projects = sub.add_parser("projects", help="案件台帳を一覧する")
    p_projects.add_argument("--stage", choices=list(STAGES), help="このステージのみ")
    p_projects.add_argument(
        "--status",
        default="active",
        choices=["active", "won", "lost", "onhold", "done", "all"],
    )
    p_projects.add_argument("--owner", help="主担当の社員 ID")
    p_projects.add_argument("--query", help="案件名・顧客名・計画地の部分一致")
    p_projects.add_argument(
        "--pipeline", action="store_true", help="ステージ別の件数だけを表示する"
    )
    p_projects.set_defaults(func=cmd_projects)

    p_project = sub.add_parser("project", help="案件 1 件の詳細と経緯を表示する")
    p_project.add_argument("project_id", help="案件 ID")
    p_project.set_defaults(func=cmd_project)

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
    except (WorkspaceError, CompanyError) as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
