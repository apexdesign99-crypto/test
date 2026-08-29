"""事務所の状況をブラウザで見るための画面。

Python 標準ライブラリだけで動く。追加インストールは要らない。

**閲覧専用。** ブラウザからは台帳を書き換えない。記録は CLI と AI社員が行う。
**127.0.0.1 のみで待ち受ける。** 施主の個人情報を扱うため、同じ社内 LAN の
他の端末からも開けない。
"""

from __future__ import annotations

import html
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .billing import totals
from .company import STAGES, OfficeProfile, ProjectLedger
from .competitor import APPEAL_AXES, CompetitorLedger
from .instagram_plan import InstagramPlan
from .workspace import Workspace, roster

NAV = (
    ("/", "ダッシュボード"),
    ("/projects", "案件"),
    ("/plan", "発信"),
    ("/billing", "請求"),
    ("/competitors", "競合"),
    ("/roster", "社員"),
)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def yen(value: int | None) -> str:
    return f"{value:,} 円" if value else "0 円"


# --------------------------------------------------------------------- 部品


def page(title: str, active: str, body: str, office_name: str) -> str:
    nav = "".join(
        f'<a href="{esc(path)}"{" class=on" if path == active else ""}>{esc(label)}</a>'
        for path, label in NAV
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — {esc(office_name or "AI社員")}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <div class="wrap bar">
    <div class="brand">{esc(office_name or "AI社員")}</div>
    <nav>{nav}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="wrap">
  閲覧専用の画面です。記録の追加・変更は CLI と AI社員が行います。
</footer>
</body>
</html>
"""


def card(label: str, value: str, tone: str = "", note: str = "") -> str:
    note_html = f'<div class="note">{esc(note)}</div>' if note else ""
    return (
        f'<div class="card {tone}"><div class="label">{esc(label)}</div>'
        f'<div class="value">{esc(value)}</div>{note_html}</div>'
    )


def section(title: str, body: str, count: int | None = None, tone: str = "") -> str:
    badge = f'<span class="badge {tone}">{count}</span>' if count is not None else ""
    return f'<section><h2>{esc(title)}{badge}</h2>{body}</section>'


def table(headers: list[str], rows: list[list[str]], empty: str = "該当なし") -> str:
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def pill(text: str, tone: str = "") -> str:
    return f'<span class="pill {tone}">{esc(text)}</span>'


def link(href: str, text: str) -> str:
    return f'<a href="{esc(href)}">{esc(text)}</a>'


# --------------------------------------------------------------------- 画面


class Views:
    """台帳を読んで画面を組み立てる。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    # 呼ぶたびに読み直す。CLI や AI社員の更新がそのまま反映される。
    @property
    def office(self) -> OfficeProfile:
        return OfficeProfile.load(self.root)

    @property
    def ledger(self) -> ProjectLedger:
        return ProjectLedger(self.root)

    @property
    def plan(self) -> InstagramPlan:
        return InstagramPlan(self.root)

    @property
    def competitors(self) -> CompetitorLedger:
        return CompetitorLedger(self.root)

    # ------------------------------------------------------- ダッシュボード

    def dashboard(self) -> str:
        ledger = self.ledger
        office = self.office
        active = ledger.list(status="active")
        stale = ledger.stale(14)
        alerts = ledger.billing_alerts(office.payment_term_days)
        overview = ledger.billing_overview()

        # ヒアリングの欠落は初回相談以降だけ見る。
        # 反響段階はまだ面談していないので、空なのが当たり前。
        heard_stages = STAGES[STAGES.index("初回相談"):]
        gaps = []
        for project in active:
            if project["stage"] not in heard_stages:
                continue
            status = ledger.hearing_gaps(project["id"])
            if status["missing_required"]:
                gaps.append((project, status))

        cards = "".join([
            card("進行中の案件", f"{len(active)} 件"),
            card("未入金", yen(overview["totals"]["outstanding"]),
                 "warn" if overview["totals"]["outstanding"] else ""),
            card("請求漏れの疑い", f"{len(alerts['unbilled'])} 件",
                 "alert" if alerts["unbilled"] else "ok",
                 yen(alerts["unbilled_amount"]) if alerts["unbilled"] else ""),
            card("追客が止まっている", f"{len(stale)} 件",
                 "alert" if stale else "ok", "14 日以上動きなし"),
        ])

        blocks = [f'<div class="cards">{cards}</div>']

        # 要対応をいちばん上に置く。良い数字より先に見せる。
        if alerts["unbilled"] or alerts["overdue"]:
            rows = [
                [link(f"/projects/{a['project_id']}", a["project_name"]),
                 esc(a["label"]), yen(a["amount"]), pill("請求漏れ", "alert")]
                for a in alerts["unbilled"]
            ] + [
                [link(f"/projects/{a['project_id']}", a["project_name"]),
                 esc(a["label"]), yen(a["amount"]),
                 pill(f"入金遅延 / 請求 {a['invoiced_at'][:10]}", "alert")]
                for a in alerts["overdue"]
            ]
            blocks.append(section(
                "請求の要対応", table(["案件", "請求の回", "金額", "状態"], rows),
                len(rows), "alert"))

        if stale:
            rows = [
                [link(f"/projects/{p['id']}", p["name"]), esc(p["stage"]),
                 esc(p.get("source") or "—"), esc(p["updated_at"][:10]),
                 esc(p.get("next_action") or "次アクション未設定")]
                for p in stale
            ]
            blocks.append(section(
                "追客が止まっている案件",
                table(["案件", "ステージ", "経路", "最終更新", "次アクション"], rows),
                len(stale), "alert"))

        if gaps:
            rows = []
            for project, status in gaps:
                missing = [m["label"] for m in status["missing_required"]]
                # 全部並べると読めないので、件数を主役にして先頭だけ見せる
                shown = "、".join(esc(label) for label in missing[:3])
                if len(missing) > 3:
                    shown += esc(f" ほか {len(missing) - 3} 件")
                rows.append([
                    link(f"/projects/{project['id']}", project["name"]),
                    pill(project["stage"]),
                    f'<strong>{len(missing)}</strong> 件',
                    shown,
                ])
            blocks.append(section(
                "提案前に確認が必要な案件",
                table(["案件", "ステージ", "未確認", "内容"], rows), len(gaps), "warn"))

        # パイプライン
        counts = ledger.pipeline()
        bars = "".join(
            f'<div class="stage"><div class="name">{esc(stage)}</div>'
            f'<div class="bar"><span style="width:{n / max(counts.values() or [1]) * 100:.0f}%"></span></div>'
            f'<div class="n">{n}</div></div>'
            for stage, n in counts.items() if n
        )
        blocks.append(section("パイプライン", f'<div class="stages">{bars}</div>'
                              if bars else '<p class="empty">進行中の案件がありません</p>'))
        return "".join(blocks)

    # ----------------------------------------------------------- 案件一覧

    def projects(self) -> str:
        ledger = self.ledger
        rows = []
        for p in ledger.list(status="all"):
            tone = {"active": "", "won": "ok", "lost": "muted", "onhold": "warn",
                    "done": "ok"}.get(p["status"], "")
            billing = (p.get("billing") or {}).get("plan", [])
            rows.append([
                link(f"/projects/{p['id']}", p["name"]),
                esc(p.get("client") or "—"),
                esc(p.get("kind") or "—"),
                pill(p["stage"]) + (pill(p["status"], tone) if p["status"] != "active" else ""),
                esc(p.get("site") or "—"),
                esc(p.get("source") or "—"),
                esc(p.get("owner") or "—"),
                esc(p.get("next_due") or "—"),
                yen(totals(billing)["outstanding"]) if billing else "—",
            ])
        return section(
            "案件台帳",
            table(["案件", "施主", "用途", "状態", "計画地", "経路", "担当", "期限", "未入金"],
                  rows, "案件がまだありません"),
            len(rows))

    # ----------------------------------------------------------- 案件詳細

    def project(self, project_id: str) -> str:
        ledger = self.ledger
        p = ledger.get(project_id)
        blocks = [f'<p class="crumb">{link("/projects", "案件台帳")} / {esc(p["name"])}</p>',
                  f'<h1>{esc(p["name"])}</h1>']

        facts = [("施主", p.get("client")), ("用途", p.get("kind")),
                 ("計画地", p.get("site")), ("流入経路", p.get("source")),
                 ("予算", p.get("budget")), ("主担当", p.get("owner")),
                 ("ステージ", p["stage"]), ("ステータス", p["status"]),
                 ("次アクション", p.get("next_action")), ("期限", p.get("next_due"))]
        blocks.append('<dl class="facts">' + "".join(
            f"<dt>{esc(k)}</dt><dd>{esc(v or '—')}</dd>" for k, v in facts) + "</dl>")

        # ヒアリング
        gaps = ledger.hearing_gaps(project_id)
        recorded = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>"
                           for k, v in gaps["recorded"].items())
        missing = "".join(f'<li>{esc(m["label"])}</li>' for m in gaps["missing_required"])
        verdict = ('<p class="ok">必須項目は揃っています。提案に進めます。</p>'
                   if gaps["ready_for_proposal"] else
                   f'<p class="alert">必須項目が未確認です。提案より先に確認してください。</p>'
                   f'<ul class="missing">{missing}</ul>')
        blocks.append(section(
            "ヒアリング",
            (f'<dl class="facts">{recorded}</dl>' if recorded
             else '<p class="empty">まだ記録がありません</p>') + verdict))

        # 敷地
        land = p.get("land")
        if land:
            result = ledger.diagnose_land(project_id, self.office)
            blocks.append(section("土地診断", f"""
<div class="cards">
  {card("建築面積の上限", f"{result['building_area_max']} ㎡", "", f"建蔽率 {result['building_coverage_applied']}%")}
  {card("延床面積の上限", f"{result['total_floor_area_max']} ㎡", "", f"容積率 {result['floor_area_ratio_applied']}%・容積対象")}
</div>
<p class="basis">{esc(result['coverage_basis'])}</p>
<p class="basis">{esc(result['floor_area_basis'])}</p>
<p class="caution">この診断では判定していない項目が {len(result['required_confirmations'])} 件あります
(斜線制限・日影規制・地区計画ほか)。{esc(result['disclaimer'])}</p>"""))

        # 請求
        plan = (p.get("billing") or {}).get("plan", [])
        if plan:
            rows = [[esc(m["id"]), esc(m["label"]), yen(m["amount"]),
                     pill(m["status"], {"入金済": "ok", "請求済": "warn"}.get(m["status"], "")),
                     esc(m["invoiced_at"][:10] if m["invoiced_at"] else "—"),
                     esc(m["paid_at"][:10] if m["paid_at"] else "—")] for m in plan]
            t = totals(plan)
            blocks.append(section("請求", table(
                ["", "回", "金額(税別)", "状態", "請求日", "入金日"], rows)
                + f'<p class="basis">入金済 {yen(t["paid"])} / 未入金 {yen(t["outstanding"])}'
                  f' / 未請求 {yen(t["unbilled"])}</p>'))

        # 掲載許諾
        status = ledger.publication_status(project_id)
        tone = "ok" if status["publishable"] else "alert"
        pubs = "".join(f'<li>{esc(x["at"][:10])} {esc(x["channel"])}: {esc(x["title"])}</li>'
                       for x in status["publications"])
        blocks.append(section("掲載許諾", (
            f'<p>{pill(status["consent_status"], tone)}'
            + (f' {esc(status["conditions"])}' if status["conditions"] else "") + "</p>"
            f'<p class="basis">{esc(status["guidance"])}</p>'
            + (f"<ul>{pubs}</ul>" if pubs else ""))))

        # 経緯
        rows = [[esc(h["at"][:16].replace("T", " ")), esc(h["by"]), esc(h["entry"])]
                for h in reversed(p["history"])]
        blocks.append(section("経緯", table(["日時", "担当", "内容"], rows), len(rows)))
        return "".join(blocks)

    # --------------------------------------------------------------- 請求

    def billing(self) -> str:
        ledger = self.ledger
        overview = ledger.billing_overview()
        alerts = ledger.billing_alerts(self.office.payment_term_days)
        g = overview["totals"]

        blocks = [f'<div class="cards">'
                  f'{card("契約額の合計", yen(g["total"]))}'
                  f'{card("入金済", yen(g["paid"]), "ok")}'
                  f'{card("未入金", yen(g["outstanding"]), "warn" if g["outstanding"] else "")}'
                  f'{card("未請求", yen(g["unbilled"]))}</div>'
                  f'<p class="basis">金額はすべて税別です。</p>']

        if alerts["unbilled"] or alerts["overdue"]:
            rows = [[link(f"/projects/{a['project_id']}", a["project_name"]),
                     pill("請求漏れ", "alert"), esc(a["label"]), yen(a["amount"]),
                     esc(a["reason"])] for a in alerts["unbilled"]]
            rows += [[link(f"/projects/{a['project_id']}", a["project_name"]),
                      pill("入金遅延", "alert"), esc(a["label"]), yen(a["amount"]),
                      esc(a["reason"])] for a in alerts["overdue"]]
            blocks.append(section("要対応", table(
                ["案件", "種別", "請求の回", "金額", "理由"], rows), len(rows), "alert"))

        rows = [[link(f"/projects/{r['project_id']}", r["project_name"]),
                 esc(r["stage"]), yen(r["total"]), yen(r["paid"]),
                 yen(r["outstanding"]), yen(r["unbilled"])]
                for r in overview["projects"]]
        blocks.append(section("案件別", table(
            ["案件", "ステージ", "契約額", "入金済", "未入金", "未請求"], rows,
            "請求計画のある案件がありません")))
        return "".join(blocks)

    # --------------------------------------------------------------- 発信

    def plan_view(self, year_month: str | None = None) -> str:
        from .workspace import now as _now

        office = self.office
        plan = self.plan
        month = year_month or _now().strftime("%Y-%m")
        posts = plan.list(month)
        gaps = plan.gaps(month, office.instagram_cadence or None)

        cards = "".join([
            card("計画", f"{gaps['planned']} 本",
                 "alert" if gaps["shortfall"] else "",
                 f"目標 {gaps['cadence']} 本" if gaps["cadence"] else "目標が未設定"),
            card("投稿済", f"{gaps['published']} 本", "ok"),
            card("素材待ち", f"{len(gaps['waiting_assets'])} 本",
                 "alert" if gaps["waiting_assets"] else "ok"),
            card("予定日超過", f"{len(gaps['overdue'])} 本",
                 "alert" if gaps["overdue"] else "ok"),
        ])
        blocks = [f'<h1>{esc(month)} の投稿計画</h1>', f'<div class="cards">{cards}</div>']
        if gaps["shortfall"]:
            blocks.append(f'<p class="alert">目標に {gaps["shortfall"]} 本足りません。</p>')

        tones = {"投稿済": "ok", "原稿済": "", "素材待ち": "alert", "見送り": "muted"}
        rows = []
        for post in posts:
            flags = []
            if not post["assets_ready"] and post["status"] != "見送り":
                flags.append(pill("素材未確認", "alert"))
            if post["scheduled_date"] < _now().date().isoformat() and post["status"] not in ("投稿済", "見送り"):
                flags.append(pill("予定日超過", "alert"))
            title = esc(post["title"]) if post["title"] else '<span class="muted">題材が未定</span>'
            if post["consent_conditions"]:
                title += f'<div class="note">掲載条件: {esc(post["consent_conditions"])}</div>'
            rows.append([
                esc(post["scheduled_date"]),
                esc(post["format_label"]),
                title,
                pill(post["status"], tones.get(post["status"], "")),
                " ".join(flags) or "—",
                (link(f"/projects/{post['project_id']}", "案件")
                 if post["project_id"] else "—"),
            ])
        blocks.append(section(
            "投稿一覧",
            table(["予定日", "型", "題材", "状態", "注意", "案件"], rows,
                  "この月の計画がありません。CLI の plan --draft で骨格を作れます。"),
            len(posts)))

        handle = f'({esc(office.instagram_handle)})' if office.instagram_handle else ""
        blocks.append(f'<p class="basis">フォロワー数や保存数は取得していません。'
                      f'Instagram の管理画面で確認した値を使ってください。{handle}</p>')
        return "".join(blocks)

    # --------------------------------------------------------------- 競合

    def competitors_view(self) -> str:
        ledger = self.competitors
        office = self.office
        own = [a for a in office.specialties if a in APPEAL_AXES]
        report = ledger.appeal_report(own_axes=own)
        records = ledger.list()

        blocks = []
        if report["competitor_count"]:
            top = max([n for _, n in report["crowded_axes"]] or [1])
            bars = "".join(
                f'<div class="stage"><div class="name">{esc(axis)}</div>'
                f'<div class="bar"><span style="width:{n / top * 100:.0f}%"></span></div>'
                f'<div class="n">{n}</div></div>'
                for axis, n in report["crowded_axes"])
            diff = "".join(f"<li>{esc(a)} {pill('競合なし', 'ok')}</li>"
                           for a in report["differentiators"])
            contested = "".join(f"<li>{esc(a)} {pill(f'競合 {n} 社と重なる', 'muted')}</li>"
                                for a, n in report["contested_axes"])
            blocks.append(section("訴求軸", f"""
<div class="two">
  <div><h3>何社が言っているか</h3><div class="stages">{bars}</div></div>
  <div><h3>自社の得意分野</h3>
    <ul class="axes">{diff}{contested}</ul>
    {'<p class="empty">事務所プロフィールの得意分野が未設定です</p>' if not own else ''}
    <p class="caution">{esc(report["caveat"])}</p>
  </div>
</div>"""))

        rows = [[esc(r["name"]), esc(r["type"]), esc(r["area"]),
                 "、".join(esc(a) for a in r["appeal_axes"]) or "—",
                 esc(r.get("instagram") or "—"),
                 f'{r["followers"]:,}' if r.get("followers") else "—",
                 esc(r["researched_at"][:10]),
                 " ".join(f'<a href="{esc(u)}" target="_blank" rel="noopener">出典</a>'
                          for u in r["sources"])]
                for r in records]
        blocks.append(section("調査済みの競合", table(
            ["会社", "業態", "商圏", "訴求軸", "Instagram", "フォロワー", "調査日", ""],
            rows, "競合がまだ登録されていません"), len(records)))
        return "".join(blocks)

    # --------------------------------------------------------------- 社員

    def roster_view(self) -> str:
        people = roster(self.root)
        ledger = self.ledger
        rows = []
        for p in people:
            workspace = Workspace(p.employee_id, self.root)
            open_tasks = len(workspace.list_tasks("open"))
            mine = ledger.list(owner=p.employee_id)
            rows.append([esc(p.name), esc(p.employee_id),
                         f"{esc(p.department)} / {esc(p.role)}",
                         pill("Web検索あり", "ok") if p.web_access else "—",
                         f"{len(mine)} 件", f"{open_tasks} 件"])
        body = table(["氏名", "ID", "所属 / 役職", "権限", "担当案件", "未完了タスク"],
                     rows, "社員がまだ採用されていません")
        readiness = "".join(
            f'<li>{pill("OK", "ok") if c["ok"] else pill("要設定", "alert")} '
            f'{esc(c["capability"])} <span class="muted">({esc(c["roles"])})</span>'
            + (f'<div class="note">{esc(c["missing"])}</div>' if not c["ok"] else "")
            + "</li>"
            for c in self.office.readiness())
        return section("在籍者", body, len(rows)) + section(
            "事務所プロフィールの充足", f'<ul class="axes">{readiness}</ul>')


CSS = """
:root{--bg:#F7F5F2;--surface:#fff;--ink:#26221E;--muted:#7C736A;--line:#E5DFD7;
--accent:#9C6B3F;--alert:#B0442E;--warn:#B07C2E;--ok:#3F6B4B;}
@media(prefers-color-scheme:dark){:root{--bg:#1C1D1F;--surface:#25272A;--ink:#EDEAE6;
--muted:#9C958C;--line:#34373B;--accent:#C89B5A;--alert:#E0765C;--warn:#D9A857;--ok:#7FB18C;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Hiragino Sans","Yu Gothic","Noto Sans JP",sans-serif;line-height:1.7;font-size:15px}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
header{background:var(--surface);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:9}
.bar{display:flex;align-items:center;gap:28px;min-height:60px;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:.04em}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav a{color:var(--muted);text-decoration:none;padding:8px 14px;border-radius:8px;font-size:14px}
nav a:hover{background:var(--bg);color:var(--ink)}
nav a.on{color:var(--accent);background:var(--bg);font-weight:700}
main{padding:32px 24px 64px}
h1{font-size:26px;margin:0 0 20px}
h2{font-size:17px;margin:0 0 14px;display:flex;align-items:center;gap:10px}
h3{font-size:14px;color:var(--muted);margin:0 0 10px;font-weight:600}
section{margin-bottom:36px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:28px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.card .label{font-size:13px;color:var(--muted)}
.card .value{font-size:27px;font-weight:700;margin-top:6px;letter-spacing:-.01em}
.card .note{font-size:12px;color:var(--muted);margin-top:4px}
.card.alert{border-color:var(--alert)}.card.alert .value{color:var(--alert)}
.card.warn{border-color:var(--warn)}.card.warn .value{color:var(--warn)}
.card.ok .value{color:var(--ok)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
table{width:100%;border-collapse:collapse;font-size:14px;min-width:620px}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:11px 16px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
a{color:var(--accent)}
.badge{background:var(--line);color:var(--muted);border-radius:20px;
padding:1px 10px;font-size:12px;font-weight:600}
.badge.alert{background:var(--alert);color:#fff}.badge.warn{background:var(--warn);color:#fff}
.pill{display:inline-block;background:var(--bg);border:1px solid var(--line);
border-radius:6px;padding:1px 9px;font-size:12px;margin-right:5px;white-space:nowrap}
.pill.alert{border-color:var(--alert);color:var(--alert)}
.pill.warn{border-color:var(--warn);color:var(--warn)}
.pill.ok{border-color:var(--ok);color:var(--ok)}
.pill.muted{color:var(--muted)}
.facts{display:grid;grid-template-columns:auto 1fr;gap:0;background:var(--surface);
border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:0 0 16px}
.facts dt{padding:10px 18px;font-size:13px;color:var(--muted);border-bottom:1px solid var(--line);white-space:nowrap}
.facts dd{padding:10px 18px;margin:0;border-bottom:1px solid var(--line)}
.facts dt:nth-last-of-type(1),.facts dd:nth-last-of-type(1){border-bottom:none}
.stages{display:grid;gap:7px}
.stage{display:grid;grid-template-columns:118px 1fr 34px;align-items:center;gap:12px;font-size:13px}
.stage .name{color:var(--muted)}
.stage .bar{background:var(--line);border-radius:4px;height:10px;overflow:hidden}
.stage .bar span{display:block;height:100%;background:var(--accent);border-radius:4px}
.stage .n{text-align:right;font-variant-numeric:tabular-nums}
.two{display:grid;grid-template-columns:1fr 1fr;gap:32px}
@media(max-width:760px){.two{grid-template-columns:1fr}.facts{grid-template-columns:1fr}
.facts dt{padding-bottom:0;border-bottom:none}}
.empty{color:var(--muted);font-size:14px;background:var(--surface);border:1px dashed var(--line);
border-radius:12px;padding:18px;margin:0}
.basis,.caution,.note{font-size:13px;color:var(--muted);margin:8px 0 0}
.caution{border-left:3px solid var(--warn);padding-left:12px}
.alert{color:var(--alert)}.ok{color:var(--ok)}.muted{color:var(--muted)}
ul.axes,ul.missing{list-style:none;padding:0;margin:0}
ul.axes li,ul.missing li{padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}
ul.axes li:last-child,ul.missing li:last-child{border-bottom:none}
.crumb{font-size:13px;color:var(--muted);margin:0 0 6px}
footer{color:var(--muted);font-size:12px;padding:20px 24px 40px;border-top:1px solid var(--line)}
"""


# --------------------------------------------------------------------- 配信


def make_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    views = Views(root)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AIEmployee"

        def log_message(self, fmt: str, *args: Any) -> None:
            """既定のアクセスログは施主名を含む URL を垂れ流すので出さない。"""

        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            # 閲覧専用の画面なので埋め込みも外部参照もさせない
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler の規約
            path = urlparse(self.path).path.rstrip("/") or "/"
            office_name = views.office.name
            try:
                if path == "/":
                    body, title, active = views.dashboard(), "ダッシュボード", "/"
                elif path == "/projects":
                    body, title, active = views.projects(), "案件", "/projects"
                elif path.startswith("/projects/"):
                    body = views.project(path.rsplit("/", 1)[-1])
                    title, active = "案件", "/projects"
                elif path == "/plan":
                    body, title, active = views.plan_view(
                        parse_qs(urlparse(self.path).query).get("month", [None])[0]
                    ), "発信", "/plan"
                elif path == "/billing":
                    body, title, active = views.billing(), "請求", "/billing"
                elif path == "/competitors":
                    body, title, active = views.competitors_view(), "競合", "/competitors"
                elif path == "/roster":
                    body, title, active = views.roster_view(), "社員", "/roster"
                else:
                    self._send(page("見つかりません", "/",
                                    '<h1>ページが見つかりません</h1>'
                                    f'<p>{link("/", "ダッシュボードへ")}</p>', office_name), 404)
                    return
            except Exception as exc:  # noqa: BLE001 - 画面を落とさず理由を見せる
                detail = esc(f"{type(exc).__name__}: {exc}")
                trace = esc(traceback.format_exc())
                self._send(page("エラー", "/", f"""<h1>表示できませんでした</h1>
<p class="alert">{detail}</p>
<p class="basis">台帳のファイルが壊れている可能性があります。
CLI で <code>python -m ai_employee doctor</code> を実行して確認してください。</p>
<details><summary>詳細</summary><pre>{trace}</pre></details>""", office_name), 500)
                return
            self._send(page(title, active, body, office_name))

    return Handler


def serve(root: Path, port: int = 8765) -> ThreadingHTTPServer:
    """画面を配信するサーバを作る。

    127.0.0.1 のみで待ち受ける。施主の個人情報を扱うため、
    同じ社内 LAN の他の端末からも開けない。
    """
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(root))
