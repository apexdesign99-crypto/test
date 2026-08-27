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
from .config import DEFAULT_MODEL, office_root
from .company import (
    CHANNELS,
    CONSENT_STATUSES,
    KINDS,
    STAGES,
    CompanyError,
    OfficeProfile,
    ProjectLedger,
)
from .billing import BILLING_STATUSES, EXAMPLE_SCHEDULE
from .competitor import APPEAL_AXES, COMPETITOR_TYPES, CompetitorError, CompetitorLedger
from .copycheck import review_copy
from .instagram import POST_FORMATS, THEMES, InstagramError
from .land import RELAXATIONS, ZONING_TYPES, LandConditions, LandError, diagnose
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


def _parse_unit_prices(raw: str) -> dict[str, list[int]]:
    """"戸建住宅:80-100,店舗:60-90" 形式を辞書に変換する。"""
    prices: dict[str, list[int]] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"坪単価の書式が不正です: {chunk} (例 戸建住宅:80-100)")
        kind, span = (part.strip() for part in chunk.split(":", 1))
        if kind not in KINDS:
            raise ValueError(f"不正な用途種別です: {kind} (選択肢: {'/'.join(KINDS)})")
        try:
            low, high = (int(v.strip()) for v in span.split("-", 1))
        except ValueError:
            raise ValueError(
                f"坪単価は「下限-上限」の整数で指定してください: {chunk}"
            ) from None
        if low <= 0 or high < low:
            raise ValueError(f"坪単価の範囲が不正です: {chunk}")
        prices[kind] = [low, high]
    if not prices:
        raise ValueError("坪単価が 1 件も指定されていません")
    return prices


def _parse_billing_schedule(raw: str) -> list[dict[str, Any]]:
    """"契約金:30:設計契約,引渡:70:竣工" 形式を配分表に変換する。"""
    schedule: list[dict[str, Any]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(":")]
        if len(parts) != 3:
            raise ValueError(
                f"請求スケジュールの書式が不正です: {chunk} "
                f"(例 契約金:30:設計契約)"
            )
        label, ratio, stage = parts
        if not label:
            raise ValueError(f"表示名が空です: {chunk}")
        if stage not in STAGES:
            raise ValueError(f"不正なステージです: {stage} (選択肢: {'/'.join(STAGES)})")
        try:
            ratio_value = int(ratio)
        except ValueError:
            raise ValueError(f"配分割合は整数で指定してください: {chunk}") from None
        if ratio_value <= 0:
            raise ValueError(f"配分割合は 1 以上で指定してください: {chunk}")
        schedule.append({"label": label, "ratio": ratio_value, "stage": stage})
    total = sum(entry["ratio"] for entry in schedule)
    if total != 100:
        raise ValueError(f"配分割合の合計が {total}% です。100% にしてください。")
    return schedule


def _check_credentials() -> tuple[bool, str]:
    """認証情報の有無を調べる。鍵そのものは絶対に表示しない。"""
    import os

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        if os.environ.get(name):
            return True, f"環境変数 {name} が設定されています"
    profile_dir = Path.home() / ".config" / "anthropic"
    if profile_dir.is_dir() and any(profile_dir.iterdir()):
        return True, f"認証プロファイル({profile_dir})が見つかりました"
    return False, "認証情報が見つかりません"


def _check_api() -> tuple[bool, str]:
    """最小のリクエストを 1 回だけ投げて疎通を確認する。"""
    try:
        import anthropic
    except ImportError:
        return False, "anthropic パッケージが未インストールです(pip install -e .)"

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=16,
            messages=[{"role": "user", "content": "ok とだけ返してください"}],
        )
        used = getattr(response.usage, "output_tokens", "?")
        return True, f"{DEFAULT_MODEL} に接続できました(出力 {used} トークン)"
    except Exception as exc:  # noqa: BLE001 - 種類を問わず理由を見せたい
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def cmd_doctor(args: argparse.Namespace) -> int:
    """初回実行の前に、足りないものと次の一手を示す。"""
    ok_mark, ng_mark = BOLD("  OK "), RED("  要対応 ")
    problems: list[str] = []

    print(BOLD("1. 実行環境"))
    print(f"{ok_mark}Python {sys.version.split()[0]}")
    try:
        import anthropic

        print(f"{ok_mark}anthropic {anthropic.__version__}")
    except ImportError:
        print(f"{ng_mark}anthropic パッケージが未インストール")
        problems.append("pip install -e .")

    print(BOLD("\n2. 認証情報"))
    has_credentials, detail = _check_credentials()
    print((ok_mark if has_credentials else ng_mark) + detail)
    if not has_credentials:
        problems.append('export ANTHROPIC_API_KEY=sk-ant-...  (または ant auth login)')

    print(BOLD("\n3. API 疎通"))
    if args.skip_api:
        print(DIM("  --skip-api が指定されたため確認していません"))
    elif not has_credentials:
        print(DIM("  認証情報がないため確認していません"))
    else:
        api_ok, detail = _check_api()
        print((ok_mark if api_ok else ng_mark) + detail)
        if not api_ok:
            problems.append("API に接続できません。上のエラー内容を確認してください")

    print(BOLD("\n4. 事務所プロフィール"))
    office = OfficeProfile.load(args.office)
    for check in office.readiness():
        if check["ok"]:
            print(f"{ok_mark}{_pad(check['capability'], 16)}" + DIM(f"({check['roles']})"))
        else:
            print(f"{ng_mark}{_pad(check['capability'], 16)}" + DIM(f"({check['roles']})"))
            print(DIM(f"        未設定: {check['missing']}"))
            print(DIM(f"        python -m ai_employee {check['fix']}"))
            problems.append(f"{check['capability']}: 事務所プロフィールの設定")

    print(BOLD("\n5. 在籍者"))
    people = roster(args.office)
    if people:
        print(f"{ok_mark}{len(people)} 名"
              + DIM(" — " + "、".join(f"{p.name}({p.employee_id})" for p in people)))
    else:
        print(f"{ng_mark}在籍者がいません")
        print(DIM("        python -m ai_employee hire-team"))
        problems.append("社員の採用")

    print(BOLD("\n6. 案件台帳"))
    ledger = ProjectLedger(args.office)
    active = ledger.list(status="active")
    total = ledger.list(status="all")
    print(f"{ok_mark}進行中 {len(active)} 件 / 全 {len(total)} 件"
          + DIM(f"  ({ledger.path})"))

    print()
    if problems:
        print(RED(f"対応が必要な項目が {len(problems)} 件あります:"))
        for item in problems:
            print(RED(f"  ・{item}"))
        print(DIM("\n未設定のままでも社員は動きますが、"
                  "該当する業務は「できない」と報告して止まります(推測で埋めない設計のため)。"))
        return 1

    print(BOLD("すべて揃っています。最初の依頼を出せます:"))
    print('  python -m ai_employee ask --id shukyaku "HPから問い合わせが入りました。…"')
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
    if args.billing_schedule is not None:
        office.billing_schedule = _parse_billing_schedule(args.billing_schedule)
        changed = True
    if args.tax_rate is not None:
        office.tax_rate = args.tax_rate
        changed = True
    if args.payment_term_days is not None:
        office.payment_term_days = args.payment_term_days
        changed = True
    if args.unit_prices is not None:
        office.unit_prices = _parse_unit_prices(args.unit_prices)
        changed = True
    if args.design_fee_rate is not None:
        office.design_fee_rate = args.design_fee_rate
        changed = True
    if args.design_fee_minimum is not None:
        office.design_fee_minimum = args.design_fee_minimum
        changed = True
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


def cmd_hearing(args: argparse.Namespace) -> int:
    """案件のヒアリング状況を表示する。提案に進んでよいかの判断材料。"""
    gaps = ProjectLedger(args.office).hearing_gaps(args.project_id)
    print(BOLD(f"[{gaps['project_id']}] {gaps['project_name']} のヒアリング状況"))

    if gaps["recorded"]:
        print(BOLD("\n  聞けている項目"))
        for label, value in gaps["recorded"].items():
            print(f"    {_pad(label, 34)}{value}")
    else:
        print(DIM("\n  聞けている項目はまだありません。"))

    if gaps["missing_required"]:
        print(RED(f"\n  提案前に必要な未確認項目 {len(gaps['missing_required'])} 件"))
        for item in gaps["missing_required"]:
            print(RED(f"    ・{item['label']}"))
    optional = [
        item for item in gaps["missing"] if item not in gaps["missing_required"]
    ]
    if optional:
        print(DIM(f"\n  その他の未確認項目 {len(optional)} 件"))
        for item in optional:
            print(DIM(f"    ・{item['label']}"))

    print()
    if gaps["ready_for_proposal"]:
        print(BOLD("  → 必須項目は揃っています。提案に進めます。"))
    else:
        print(RED("  → 必須項目が未確認です。提案より先に確認してください。"))
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    """工事費と設計監理料の概算を算定する。"""
    office = OfficeProfile.load(args.office)
    result = office.estimate(
        args.kind, floor_area_tsubo=args.tsubo, floor_area_sqm=args.sqm
    )
    cost = result["construction_cost"]
    fee = result["design_fee"]
    print(BOLD(f"{result['kind']} 延床 {result['floor_area_tsubo']} 坪 "
               f"({result['floor_area_sqm']} ㎡) の概算"))
    print(f"  {_pad('坪単価', 14)}{result['unit_price_range'][0]}〜"
          f"{result['unit_price_range'][1]} 万円/坪")
    print(f"  {_pad('工事費', 14)}{cost['low']:,}〜{cost['high']:,} 万円")
    note = f"工事費の {fee['rate_percent']}%"
    if fee["applied_minimum"]:
        note += f"(最低額 {fee['minimum']:,} 万円を適用)"
    print(f"  {_pad('設計監理料', 14)}{fee['low']:,}〜{fee['high']:,} 万円  " + DIM(note))
    print()
    print(DIM(f"  {result['caveat']}"))
    return 0


def cmd_consent(args: argparse.Namespace) -> int:
    """掲載許諾を記録・確認する。"""
    ledger = ProjectLedger(args.office)
    if args.status:
        ledger.record_consent(args.project_id, args.status, args.conditions or "")
    status = ledger.publication_status(args.project_id)

    mark = BOLD if status["publishable"] else RED
    print(BOLD(f"[{status['project_id']}] {status['project_name']}"))
    print(f"  掲載許諾: {mark(status['consent_status'])}")
    if status["conditions"]:
        print(f"  条件    : {status['conditions']}")
    print(f"  → {status['guidance']}")
    if status["publications"]:
        print(BOLD(f"\n  発信履歴 ({len(status['publications'])} 件)"))
        for pub in status["publications"]:
            url = f"  {pub['url']}" if pub["url"] else ""
            print(f"    {pub['at'][:10]}  {_pad(pub['channel'], 16)}{pub['title']}{DIM(url)}")
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    """発信ネタの棚卸し。"""
    result = ProjectLedger(args.office).publication_candidates(
        channel=args.channel, kind=args.kind
    )
    scope = f"({args.channel} で未発信)" if args.channel else ""
    print(BOLD(f"発信できる案件 {len(result['ready'])} 件 ") + DIM(scope))
    for item in result["ready"]:
        published = (
            DIM("  既出: " + "、".join(item["published_channels"]))
            if item["published_channels"]
            else ""
        )
        print(f"  [{item['id']}] {_pad(item['name'], 24)}{item['consent_status']}{published}")
        if item["conditions"]:
            print(DIM(f"        条件: {item['conditions']}"))

    if result["needs_consent"]:
        print(RED(f"\n先に施主の許諾が必要な案件 {len(result['needs_consent'])} 件"))
        for item in result["needs_consent"]:
            print(f"  [{item['id']}] {_pad(item['name'], 24)}{RED(item['consent_status'])}")
        print(DIM("  許諾が取れるまで、これらを題材にした原稿は書けません。"))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """原稿の表現をチェックする。"""
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    result = review_copy(text)

    if not result["count"]:
        print(BOLD("既知のパターンでの指摘はありません。"))
    else:
        print(BOLD(f"確認が要る箇所 {result['count']} 件"))
        for flag in result["flags"]:
            print(RED(f"  L{flag['line']} [{flag['category']}] {flag['phrase']}"))
            print(DIM(f"      {flag['context']}"))
            print(f"      {flag['reason']}")
    print(DIM(f"\n※ {result['disclaimer']}"))
    return 1 if result["count"] else 0


def _yen(value: int | None) -> str:
    return f"{value:,} 円" if value is not None else "-"


def _print_diagnosis(result: dict[str, Any]) -> None:
    """診断結果を表示する。但し書きと確認事項は必ず出す。"""
    building = result["building_area_max"]
    floor = result["total_floor_area_max"]
    tsubo = round(floor / (400 / 121), 1)

    print(BOLD(f"{result['zoning']} / 敷地 {result['site_area']}㎡"))
    print()
    print(f"  建築面積の上限  {BOLD(str(building) + ' ㎡')}"
          f"  (建蔽率 {result['building_coverage_applied']}%)")
    print(f"  延床面積の上限  {BOLD(str(floor) + ' ㎡')}"
          f"  (容積率 {result['floor_area_ratio_applied']}% / 約 {tsubo} 坪・容積対象)")
    print()
    print(DIM(f"  {result['coverage_basis']}"))
    print(DIM(f"  {result['floor_area_basis']}"))

    check = result["road_check"]
    print()
    if check["judged"]:
        verdict = BOLD("満たしている") if check["passes"] else RED("満たしていない")
        print(f"  接道義務: {verdict}")
        print(DIM(f"    {check['basis']}"))
    else:
        print(DIM("  接道義務: 前面道路の情報が未入力のため判定していません。"))

    if result["missing_inputs"]:
        print(RED(f"\n  未入力の項目 {len(result['missing_inputs'])} 件"))
        for item in result["missing_inputs"]:
            print(RED(f"    ・{item}"))

    print(BOLD(f"\n  この診断では判定していない項目 "
               f"{len(result['required_confirmations'])} 件"))
    print(DIM("  すべて所管行政庁・都市計画情報で確認が必要です。"))
    for item in result["required_confirmations"]:
        print(f"    ・{_pad(item['item'], 12)}{DIM(item['detail'])}")

    print(RED(f"\n※ {result['disclaimer']}"))


def cmd_competitors(args: argparse.Namespace) -> int:
    """調査済みの競合と、訴求軸の集計を表示する。"""
    ledger = CompetitorLedger(args.office)

    if args.axes:
        office = OfficeProfile.load(args.office)
        own = [a for a in office.specialties if a in APPEAL_AXES]
        report = ledger.appeal_report(area=args.area, own_axes=own)
        scope = f"({args.area})" if args.area else "(全エリア)"
        print(BOLD(f"訴求軸の集計 {scope}") + DIM(f"  調査済み {report['competitor_count']} 社"))
        if not report["competitor_count"]:
            print(DIM("  競合が 1 社も登録されていません。まず調査して登録してください。"))
            return 0
        print(BOLD("\n  何社が言っているか"))
        for axis, count in report["crowded_axes"]:
            print(f"    {_pad(axis, 24)}{'■' * count} {count} 社")
        if report["empty_axes"]:
            print(BOLD("\n  誰も言っていない軸"))
            print(DIM("    " + "、".join(report["empty_axes"])))
        if own:
            print(BOLD("\n  自社の得意分野との突き合わせ"))
            for axis in report["differentiators"]:
                print(f"    {_pad(axis, 24)}" + BOLD("競合なし — 差別化の候補"))
            for axis, count in report["contested_axes"]:
                print(f"    {_pad(axis, 24)}" + DIM(f"競合 {count} 社と重なる"))
        else:
            print(DIM("\n  事務所プロフィールの得意分野が未設定のため、"
                      "自社との突き合わせはできません(office --specialties)。"))
        print(DIM(f"\n  ※ {report['caveat']}"))
        return 0

    records = ledger.list(area=args.area, company_type=args.type)
    if not records:
        print("該当する競合は登録されていません。")
        return 0
    print(BOLD(f"調査済みの競合 {len(records)} 社") + DIM("(新しく調べた順)"))
    for record in records:
        followers = f"  {record['followers']:,}フォロワー" if record["followers"] else ""
        print(BOLD(f"  [{record['id']}] {record['name']}")
              + f"  {record['type']}  {record['area']}")
        if record["appeal_axes"]:
            print(f"      訴求: {'、'.join(record['appeal_axes'])}")
        if record["instagram"] or followers:
            print(DIM(f"      {record['instagram']}{followers}"
                      f"  {record['post_frequency']}"))
        if record["price_range"]:
            print(DIM(f"      価格帯: {record['price_range']}"))
        print(DIM(f"      調査 {record['researched_at'][:10]} / 出典 "
                  + "、".join(record["sources"])))
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    """Instagram 投稿の型を一覧する。"""
    if args.format:
        data = POST_FORMATS[args.format]
        print(BOLD(f"{data['label']} ({args.format})"))
        print(f"  ねらい: {data['purpose']}")
        print(BOLD("\n  1枚目のフック"))
        print(f"    {data['hook']}")
        print(BOLD("\n  構成"))
        for index, slide in enumerate(data["slides"], 1):
            print(f"    {index}. {slide}")
        print(BOLD("\n  必要な素材") + DIM("(揃わないなら別の型を選ぶ)"))
        for asset in data["assets"]:
            print(f"    ・{asset}")
        return 0

    print(BOLD("Instagram 投稿の型"))
    for key, data in POST_FORMATS.items():
        print(f"  {_pad(key, 14)}{_pad(data['label'], 24)}{DIM(data['purpose'])}")
    print(BOLD("\n配色"))
    for key, data in THEMES.items():
        print(f"  {_pad(key, 14)}{data['label']}")
    print(DIM("\n型の詳細: python -m ai_employee post --format works"))
    print(DIM("デザインの生成は社員に依頼する: "
              'ask --id shukyaku "施工事例の投稿を作って"'))
    return 0


def cmd_land(args: argparse.Namespace) -> int:
    """土地診断。案件に記録された条件、または直接指定した条件で診断する。"""
    office = OfficeProfile.load(args.office)

    if args.project:
        ledger = ProjectLedger(args.office)
        if args.site_area is not None:
            ledger.record_land(
                args.project,
                LandConditions(
                    site_area=args.site_area,
                    zoning=args.zoning,
                    building_coverage=args.coverage,
                    floor_area_ratio=args.far,
                    road_width=args.road_width,
                    road_contact=args.road_contact,
                    relaxations=args.relaxation or [],
                    note=args.note or "",
                ),
            )
        result = ledger.diagnose_land(args.project, office)
        print(BOLD(f"[{result['project_id']}] {result['project_name']}"))
        print(DIM(f"敷地条件の記録: {result['recorded_at'][:16]} by {result['recorded_by']}"))
        print()
    else:
        result = diagnose(
            LandConditions(
                site_area=args.site_area,
                zoning=args.zoning,
                building_coverage=args.coverage,
                floor_area_ratio=args.far,
                road_width=args.road_width,
                road_contact=args.road_contact,
                relaxations=args.relaxation or [],
            ),
            office.land(),
        )

    _print_diagnosis(result)
    return 0


def cmd_billing(args: argparse.Namespace) -> int:
    """請求状況の確認・請求計画の作成・請求と入金の記録。"""
    ledger = ProjectLedger(args.office)
    office = OfficeProfile.load(args.office)

    if args.alerts:
        term = args.payment_term_days or office.payment_term_days
        result = ledger.billing_alerts(term)
        if result["unbilled"]:
            print(RED(f"請求漏れの疑い {len(result['unbilled'])} 件 "
                      f"(計 {result['unbilled_amount']:,} 円)"))
            for item in result["unbilled"]:
                print(f"  [{item['project_id']}] {_pad(item['project_name'], 22)}"
                      f"{_pad(item['label'], 16)}{_yen(item['amount'])}")
                print(DIM(f"      {item['reason']}"))
        if result["overdue"]:
            print(RED(f"\n入金遅延 {len(result['overdue'])} 件 "
                      f"(計 {result['overdue_amount']:,} 円)"))
            for item in result["overdue"]:
                print(f"  [{item['project_id']}] {_pad(item['project_name'], 22)}"
                      f"{_pad(item['label'], 16)}{_yen(item['amount'])}"
                      f"  請求 {item['invoiced_at'][:10]}")
        if not result["unbilled"] and not result["overdue"]:
            print(f"請求漏れ・入金遅延({term} 日基準)はありません。")
        return 0

    if not args.project:
        overview = ledger.billing_overview()
        if not overview["projects"]:
            print("請求計画のある案件がありません。")
            return 0
        print(BOLD("案件別の請求状況") + DIM("(未入金の多い順・すべて税別)"))
        header = (
            _pad("案件", 26) + _ralign("契約額", 14) + _ralign("入金済", 14)
            + _ralign("未入金", 14) + _ralign("未請求", 14)
        )
        print(DIM("  " + header))
        for row in overview["projects"]:
            print(f"  {_pad(row['project_name'], 26)}"
                  f"{row['total']:>14,}{row['paid']:>14,}"
                  f"{row['outstanding']:>14,}{row['unbilled']:>14,}")
        grand = overview["totals"]
        print(DIM("  " + "-" * 82))
        print(f"  {_pad('合計', 26)}{grand['total']:>14,}{grand['paid']:>14,}"
              f"{grand['outstanding']:>14,}{grand['unbilled']:>14,}")
        return 0

    if args.setup is not None:
        ledger.setup_billing(args.project, args.setup, office)
    for milestone_id, status in (
        (args.invoiced, "請求済"),
        (args.paid, "入金済"),
        (args.reset, "未請求"),
    ):
        if milestone_id:
            ledger.update_billing(args.project, milestone_id, status=status)

    status = ledger.billing_status(args.project)
    print(BOLD(f"[{status['project_id']}] {status['project_name']}")
          + f"  {status['stage']}")
    if not status["configured"]:
        print(DIM("  請求計画は未作成です。--setup <契約金額(円)> で作成できます。"))
        return 0

    print(f"  契約金額: {_yen(status['contract_amount'])} " + DIM("(税別)"))
    print()
    for milestone in status["plan"]:
        mark = {"入金済": BOLD, "請求済": CYAN, "未請求": DIM}[milestone["status"]]
        dates = []
        if milestone["invoiced_at"]:
            dates.append(f"請求 {milestone['invoiced_at'][:10]}")
        if milestone["paid_at"]:
            dates.append(f"入金 {milestone['paid_at'][:10]}")
        print(f"  {milestone['id']}  {_pad(milestone['label'], 18)}"
              f"{milestone['amount']:>12,}  {mark(_pad(milestone['status'], 8))}"
              + DIM("  ".join(dates)))
        if milestone["note"]:
            print(DIM(f"        {milestone['note']}"))

    t = status["totals"]
    print()
    print(f"  入金済 {t['paid']:,} / 未入金 {t['outstanding']:,} / 未請求 {t['unbilled']:,} 円")
    if office.tax_rate is None:
        print(DIM("  消費税率が未設定のため、税込金額は表示していません。"))
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

    p_doctor = sub.add_parser(
        "doctor", help="初回実行の前に、足りないものと次の一手を確認する"
    )
    p_doctor.add_argument(
        "--skip-api", action="store_true", help="API 疎通の確認を省略する"
    )
    p_doctor.set_defaults(func=cmd_doctor)

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
    p_office.add_argument(
        "--unit-prices",
        dest="unit_prices",
        help='概算の坪単価。"戸建住宅:80-100,店舗:60-90" 形式(万円/坪)',
    )
    p_office.add_argument(
        "--design-fee-rate", dest="design_fee_rate", type=float, help="設計監理料率 (%%)"
    )
    p_office.add_argument(
        "--design-fee-minimum",
        dest="design_fee_minimum",
        type=int,
        help="設計監理料の最低額(万円)",
    )
    p_office.add_argument(
        "--billing-schedule",
        dest="billing_schedule",
        help="出来高払いの配分。"
        + '"' + ",".join(f"{l}:{r}:{s}" for l, r, s in EXAMPLE_SCHEDULE) + '" 形式',
    )
    p_office.add_argument("--tax-rate", dest="tax_rate", type=float, help="消費税率 (%%)")
    p_office.add_argument(
        "--payment-term-days",
        dest="payment_term_days",
        type=int,
        help="入金遅延とみなす日数(既定 30)",
    )
    p_office.set_defaults(func=cmd_office)

    p_competitors = sub.add_parser("competitors", help="調査済みの競合と訴求軸の集計")
    p_competitors.add_argument("--area", help="商圏の部分一致 (例 愛知)")
    p_competitors.add_argument("--type", choices=list(COMPETITOR_TYPES), help="業態で絞る")
    p_competitors.add_argument(
        "--axes", action="store_true", help="訴求軸の集計と差別化候補を表示する"
    )
    p_competitors.set_defaults(func=cmd_competitors)

    p_post = sub.add_parser("post", help="Instagram 投稿の型を一覧する")
    p_post.add_argument("--format", choices=list(POST_FORMATS), help="型の詳細を表示する")
    p_post.set_defaults(func=cmd_post)

    p_land = sub.add_parser(
        "land", help="土地診断(建てられるボリュームの目安と、確認すべき論点)"
    )
    p_land.add_argument("--project", help="案件 ID。省略時は指定条件で単発診断する")
    p_land.add_argument("--site-area", dest="site_area", type=float, help="敷地面積(㎡)")
    p_land.add_argument("--zoning", choices=list(ZONING_TYPES), help="用途地域")
    p_land.add_argument("--coverage", type=float, help="指定建蔽率 (%%)")
    p_land.add_argument("--far", type=float, help="指定容積率 (%%)")
    p_land.add_argument("--road-width", dest="road_width", type=float, help="前面道路幅員(m)")
    p_land.add_argument("--road-contact", dest="road_contact", type=float, help="接道長さ(m)")
    p_land.add_argument(
        "--relaxation",
        action="append",
        choices=[key for key, _ in RELAXATIONS],
        help="行政に適用を確認できた建蔽率の緩和(複数指定可)",
    )
    p_land.add_argument("--note", help="調査時の補足・出典")
    p_land.set_defaults(func=cmd_land)

    p_billing = sub.add_parser("billing", help="請求状況の確認と、請求・入金の記録")
    p_billing.add_argument("--project", help="案件 ID(明細を見る/更新する)")
    p_billing.add_argument(
        "--alerts", action="store_true", help="請求漏れと入金遅延だけを表示する"
    )
    p_billing.add_argument(
        "--setup", type=int, metavar="金額", help="契約金額(円)から請求計画を作る"
    )
    p_billing.add_argument("--invoiced", metavar="回ID", help="この回を請求済にする")
    p_billing.add_argument("--paid", metavar="回ID", help="この回を入金済にする")
    p_billing.add_argument("--reset", metavar="回ID", help="この回を未請求に戻す")
    p_billing.add_argument(
        "--payment-term-days", dest="payment_term_days", type=int, help="入金遅延の判定日数"
    )
    p_billing.set_defaults(func=cmd_billing)

    p_hearing = sub.add_parser("hearing", help="案件のヒアリング状況を確認する")
    p_hearing.add_argument("project_id", help="案件 ID")
    p_hearing.set_defaults(func=cmd_hearing)

    p_estimate = sub.add_parser("estimate", help="工事費と設計監理料の概算を算定する")
    p_estimate.add_argument("--kind", required=True, choices=list(KINDS), help="用途種別")
    p_estimate.add_argument("--tsubo", type=float, help="延床面積(坪)")
    p_estimate.add_argument("--sqm", type=float, help="延床面積(㎡)")
    p_estimate.set_defaults(func=cmd_estimate)

    p_consent = sub.add_parser("consent", help="掲載許諾を記録・確認する")
    p_consent.add_argument("project_id", help="案件 ID")
    p_consent.add_argument(
        "--status", choices=list(CONSENT_STATUSES), help="許諾状態を記録する"
    )
    p_consent.add_argument("--conditions", help="条件付きの場合の条件")
    p_consent.set_defaults(func=cmd_consent)

    p_candidates = sub.add_parser("candidates", help="発信ネタを棚卸しする")
    p_candidates.add_argument(
        "--channel", choices=list(CHANNELS), help="このチャネルで未発信のものに絞る"
    )
    p_candidates.add_argument("--kind", choices=list(KINDS), help="用途種別で絞る")
    p_candidates.set_defaults(func=cmd_candidates)

    p_check = sub.add_parser("check", help="原稿の表現をチェックする")
    check_source = p_check.add_mutually_exclusive_group(required=True)
    check_source.add_argument("--file", help="チェックするファイル")
    check_source.add_argument("--text", help="チェックする本文")
    p_check.set_defaults(func=cmd_check)

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
    except (WorkspaceError, CompanyError, LandError, CompetitorError, InstagramError) as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(RED(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
