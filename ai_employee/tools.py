"""AI社員が使える業務ツール。

各ツールは Claude に渡す JSON Schema と、実際に実行される Python 関数の組。
プロフィールの `tools` に列挙された名前だけが有効になる(権限管理)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .billing import BILLING_STATUSES, with_tax
from .company import (
    CHANNELS,
    CONSENT_STATUSES,
    HEARING_ITEMS,
    HEARING_KEYS,
    KINDS,
    STAGES,
    STATUSES,
    CompanyError,
    OfficeProfile,
    ProjectLedger,
)
from .competitor import APPEAL_AXES, COMPETITOR_TYPES, CompetitorError, CompetitorLedger
from .copycheck import review_copy as _review_copy
from .instagram import (
    POST_FORMATS,
    THEMES,
    InstagramError,
    build_design,
    post_format as _post_format,
)
from .land import RELAXATIONS, ZONING_TYPES, LandConditions
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
    competitors = CompetitorLedger(workspace.root.parent)
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

    def record_consent(project_id: str, status: str, conditions: str = "") -> dict:
        updated = ledger.record_consent(project_id, status, conditions, by=me)
        return {"id": updated["id"], "consent": updated["consent"]}

    def publication_status(project_id: str) -> dict:
        return ledger.publication_status(project_id)

    def log_publication(
        project_id: str, channel: str, title: str, url: str = ""
    ) -> dict:
        return ledger.log_publication(project_id, channel, title, url, by=me)

    def publication_candidates(channel: str | None = None, kind: str | None = None) -> dict:
        return ledger.publication_candidates(channel=channel, kind=kind)

    def review_copy(text: str) -> dict:
        return _review_copy(text)

    def record_competitor(
        name: str,
        area: str,
        sources: list[str],
        company_type: str = "その他",
        appeal_axes: list[str] | None = None,
        price_range: str = "",
        instagram: str = "",
        followers: int | None = None,
        post_frequency: str = "",
        strengths: str = "",
        note: str = "",
    ) -> dict:
        return competitors.record(
            name=name, area=area, sources=sources, company_type=company_type,
            appeal_axes=appeal_axes, price_range=price_range, instagram=instagram,
            followers=followers, post_frequency=post_frequency,
            strengths=strengths, note=note, by=me,
        )

    def list_competitors(area: str | None = None, company_type: str | None = None) -> dict:
        hits = competitors.list(area=area, company_type=company_type)
        return {"count": len(hits), "competitors": hits}

    def appeal_report(area: str | None = None) -> dict:
        # 自社の訴求軸は事務所プロフィールの得意分野から拾う。
        own = [a for a in office.specialties if a in APPEAL_AXES]
        return competitors.appeal_report(area=area, own_axes=own)

    def post_formats() -> dict:
        return {
            "formats": [
                {"key": key, **value} for key, value in POST_FORMATS.items()
            ],
            "themes": [{"key": key, "label": v["label"]} for key, v in THEMES.items()],
        }

    def build_post_design(
        path: str,
        slides: list[dict],
        theme: str = "wood",
        brand: str | None = None,
    ) -> dict:
        html = build_design(
            slides,
            theme=theme,
            brand=brand if brand is not None else office.name,
        )
        saved = workspace.write_file(path, html)
        return {
            "path": path,
            "saved_to": str(saved),
            "slides": len(slides),
            "theme": theme,
            "how_to_export": "ブラウザでこの HTML を開き、各スライドを "
            "1080×1080 でスクリーンショットするか、印刷ダイアログから PDF に出す。"
            "画像そのものはこのツールでは生成できない。",
        }

    def record_land(
        project_id: str,
        site_area: float,
        zoning: str,
        building_coverage: float,
        floor_area_ratio: float,
        road_width: float | None = None,
        road_contact: float | None = None,
        relaxations: list[str] | None = None,
        note: str = "",
    ) -> dict:
        return ledger.record_land(
            project_id,
            LandConditions(
                site_area=site_area,
                zoning=zoning,
                building_coverage=building_coverage,
                floor_area_ratio=floor_area_ratio,
                road_width=road_width,
                road_contact=road_contact,
                relaxations=relaxations or [],
                note=note,
            ),
            by=me,
        )

    def diagnose_land(project_id: str) -> dict:
        return ledger.diagnose_land(project_id, office)

    def setup_billing(project_id: str, contract_amount: int) -> dict:
        return ledger.setup_billing(project_id, contract_amount, office, by=me)

    def update_billing(
        project_id: str,
        milestone_id: str,
        status: str | None = None,
        amount: int | None = None,
        note: str | None = None,
    ) -> dict:
        return ledger.update_billing(
            project_id, milestone_id, status=status, amount=amount, note=note, by=me
        )

    def billing_status(project_id: str) -> dict:
        return ledger.billing_status(project_id)

    def billing_alerts(payment_term_days: int | None = None) -> dict:
        return ledger.billing_alerts(
            payment_term_days
            if payment_term_days is not None
            else office.payment_term_days
        )

    def billing_overview() -> dict:
        return ledger.billing_overview()

    def tax_breakdown(amount: int) -> dict:
        return with_tax(amount, office.tax_rate)

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
            "publication_status",
            "この案件を発信してよいか、掲載許諾の状態と条件を返す。"
            "施工事例・SNS 投稿・記事など、案件を題材にした原稿を書く前に必ず呼ぶこと。"
            "許諾が未確認または不可なら、原稿を書いてはいけない。",
            _obj({"project_id": {"type": "string", "description": "案件 ID"}}, ["project_id"]),
            publication_status,
        ),
        Tool(
            "record_consent",
            "施主から得た掲載許諾を記録する。施主に確認した事実だけを記録すること。"
            "確認していない許諾を「許諾済」にしてはいけない。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "status": {
                        "type": "string",
                        "enum": list(CONSENT_STATUSES),
                        "description": "許諾の状態",
                    },
                    "conditions": {
                        "type": "string",
                        "description": "条件付きの場合の条件(必須)。"
                        "例: 施主名は伏せる、外観写真のみ、所在地は市区まで",
                    },
                },
                ["project_id", "status"],
            ),
            record_consent,
        ),
        Tool(
            "log_publication",
            "案件を発信したことを記録する。どのチャネルで何を出したかが残り、"
            "publication_candidates での重複を防げる。許諾のない案件には記録できない。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "channel": {
                        "type": "string",
                        "enum": list(CHANNELS),
                        "description": "発信チャネル",
                    },
                    "title": {"type": "string", "description": "発信物のタイトル"},
                    "url": {"type": "string", "description": "公開 URL(あれば)"},
                },
                ["project_id", "channel", "title"],
            ),
            log_publication,
        ),
        Tool(
            "publication_candidates",
            "発信ネタの棚卸し。許諾があってまだ出していない案件(ready)と、"
            "先に施主の許諾が必要な案件(needs_consent)に分けて返す。"
            "ネタを探すときは推測せずこれを使うこと。",
            _obj(
                {
                    "channel": {
                        "type": "string",
                        "enum": list(CHANNELS),
                        "description": "このチャネルで未発信のものに絞る",
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(KINDS),
                        "description": "用途種別で絞る",
                    },
                },
                [],
            ),
            publication_candidates,
        ),
        Tool(
            "review_copy",
            "原稿の表現をチェックし、確認が要る箇所を返す。"
            "最上級・断定・比較優位の表現、個人が特定される情報、裏付けが要る数値を拾う。"
            "社外に出る原稿を提示する前に必ず自分の原稿を通し、指摘された箇所を直すか、"
            "直せない理由を報告に書くこと。"
            "これは適法性の判断ではなく、既知パターンの機械的チェックである。",
            _obj(
                {"text": {"type": "string", "description": "チェックする原稿の全文"}},
                ["text"],
            ),
            review_copy,
        ),
        Tool(
            "record_competitor",
            "周辺の住宅会社・工務店・設計事務所を競合台帳に登録する。"
            "**出典 URL が必須。** 記憶や推測で他社の情報を書いてはいけない。"
            "web_search / web_fetch で公開情報を確認し、その URL を sources に渡すこと。"
            "確認できなかった項目は空のままにする。",
            _obj(
                {
                    "name": {"type": "string", "description": "会社名"},
                    "area": {"type": "string", "description": "商圏 (例 愛知県一宮市)"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "情報源の URL(必須。1 件以上)",
                    },
                    "company_type": {
                        "type": "string",
                        "enum": list(COMPETITOR_TYPES),
                        "description": "業態",
                    },
                    "appeal_axes": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(APPEAL_AXES)},
                        "description": "その会社が前面に出している訴求軸",
                    },
                    "price_range": {
                        "type": "string",
                        "description": "公開されている価格帯のみ。書かれていなければ空にする。",
                    },
                    "instagram": {"type": "string", "description": "Instagram アカウント"},
                    "followers": {"type": "integer", "description": "フォロワー数(確認できた時点の値)"},
                    "post_frequency": {"type": "string", "description": "投稿頻度 (例 週3回)"},
                    "strengths": {"type": "string", "description": "打ち出している強み"},
                    "note": {"type": "string", "description": "補足"},
                },
                ["name", "area", "sources"],
            ),
            record_competitor,
        ),
        Tool(
            "list_competitors",
            "調査済みの競合を新しい順に返す。競合の話をする前に必ずこれで"
            "「実際に調べた範囲」を確認すること。台帳にない会社を語らない。",
            _obj(
                {
                    "area": {"type": "string", "description": "商圏の部分一致 (例 愛知)"},
                    "company_type": {
                        "type": "string",
                        "enum": list(COMPETITOR_TYPES),
                        "description": "業態で絞る",
                    },
                },
                [],
            ),
            list_competitors,
        ),
        Tool(
            "appeal_report",
            "競合の訴求軸を集計し、混んでいる軸と空いている軸を出す。"
            "自社の得意分野(事務所プロフィール)と突き合わせ、差別化できる軸を示す。"
            "空いている軸は需要がない可能性もあるので、断定せず判断材料として扱うこと。",
            _obj({"area": {"type": "string", "description": "商圏で絞る (例 愛知)"}}, []),
            appeal_report,
        ),
        Tool(
            "post_formats",
            "Instagram 投稿の型(施工事例・ビフォーアフター・豆知識・お客様の声・"
            "イベント告知・スタッフ)と、各型の 1 枚目のフック・構成・必要な素材を返す。"
            "投稿を企画する前にこれを見て型を選ぶこと。素材が揃わない型は選ばない。",
            _obj({}, []),
            post_formats,
        ),
        Tool(
            "build_post_design",
            "Instagram 投稿のデザインを 1080×1080 の HTML として保存する。"
            "**画像そのものは作れない。** 保存した HTML をブラウザで開いて"
            "スクリーンショットするか PDF 出力して画像化する、と必ず報告に書くこと。"
            "写真は入らないので、写真を入れる位置は原稿側で指示する。",
            _obj(
                {
                    "path": {
                        "type": "string",
                        "description": "保存先 (例 instagram/2026-09-works.html)",
                    },
                    "slides": {
                        "type": "array",
                        "description": "スライドの配列。最初は kind=cover、最後は kind=cta にする。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["cover", "body", "cta"],
                                    "description": "表紙 / 本文 / 締め",
                                },
                                "label": {"type": "string", "description": "小見出し (例 課題)"},
                                "title": {"type": "string", "description": "見出し(必須)"},
                                "body": {"type": "string", "description": "本文"},
                            },
                            "required": ["title"],
                            "additionalProperties": False,
                        },
                    },
                    "theme": {
                        "type": "string",
                        "enum": list(THEMES),
                        "description": "配色",
                    },
                    "brand": {
                        "type": "string",
                        "description": "各スライド下部に入れる事務所名。省略時は事務所プロフィールの名称。",
                    },
                },
                ["path", "slides"],
            ),
            build_post_design,
        ),
        Tool(
            "record_land",
            "調べた敷地条件を案件に記録する。"
            "用途地域・建蔽率・容積率は、都市計画情報や役所で確認した値だけを入れること。"
            "**推測して埋めてはいけない。** 分からない項目は渡さず、"
            "何を調べる必要があるかを報告する。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "site_area": {"type": "number", "description": "敷地面積(㎡)"},
                    "zoning": {
                        "type": "string",
                        "enum": list(ZONING_TYPES),
                        "description": "用途地域(都市計画情報で確認した値)",
                    },
                    "building_coverage": {
                        "type": "number",
                        "description": "指定建蔽率(%)。緩和前の指定値。",
                    },
                    "floor_area_ratio": {
                        "type": "number",
                        "description": "指定容積率(%)",
                    },
                    "road_width": {"type": "number", "description": "前面道路幅員(m)"},
                    "road_contact": {"type": "number", "description": "接道長さ(m)"},
                    "relaxations": {
                        "type": "array",
                        "items": {"type": "string", "enum": [key for key, _ in RELAXATIONS]},
                        "description": "行政に適用を確認できた建蔽率の緩和のみ。"
                        "確認できていないものは入れない。",
                    },
                    "note": {"type": "string", "description": "調査時の補足・出典"},
                },
                ["project_id", "site_area", "zoning", "building_coverage", "floor_area_ratio"],
            ),
            record_land,
        ),
        Tool(
            "diagnose_land",
            "記録済みの敷地条件から、建築面積・延床面積の上限と接道義務の判定を計算する。"
            "計算はツール側が行うので、自分で掛け算をしないこと。"
            "これは法適合の判断ではなく、斜線制限・日影規制・地区計画は計算していない。"
            "結果の required_confirmations を必ず報告に含め、"
            "disclaimer をそのまま添えること。",
            _obj({"project_id": {"type": "string", "description": "案件 ID"}}, ["project_id"]),
            diagnose_land,
        ),
        Tool(
            "setup_billing",
            "契約金額から出来高払いの請求計画を作る。設計契約が成立したら作成すること。"
            "金額は円単位の整数で渡す。割り付けと端数調整はツール側で行うので、"
            "自分で各回の金額を計算しない。既に請求済の回があると作り直せない。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "contract_amount": {
                        "type": "integer",
                        "description": "設計監理料の契約金額(円・税別)。原本で確認した金額のみ。",
                    },
                },
                ["project_id", "contract_amount"],
            ),
            setup_billing,
        ),
        Tool(
            "update_billing",
            "請求の 1 回分を更新する。請求書を出したら請求済に、入金を確認したら入金済にする。"
            "入金の確認は通帳や入金記録で裏を取ってから記録すること。",
            _obj(
                {
                    "project_id": {"type": "string", "description": "案件 ID"},
                    "milestone_id": {"type": "string", "description": "請求の回の ID (例 m2)"},
                    "status": {
                        "type": "string",
                        "enum": list(BILLING_STATUSES),
                        "description": "新しい状態",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "金額を直す場合の新しい金額(円・税別)",
                    },
                    "note": {"type": "string", "description": "備考"},
                },
                ["project_id", "milestone_id"],
            ),
            update_billing,
        ),
        Tool(
            "billing_status",
            "1 案件の請求計画と、請求済・入金済・未請求の合計を返す。"
            "金額を報告する前に必ずこれで実際の数字を確認すること。",
            _obj({"project_id": {"type": "string", "description": "案件 ID"}}, ["project_id"]),
            billing_status,
        ),
        Tool(
            "billing_alerts",
            "請求漏れと入金遅延を洗い出す。請求漏れは案件のステージが請求条件に達しているのに"
            "未請求の回、入金遅延は請求済のまま支払期日を過ぎた回。"
            "請求まわりの報告をする前に必ずこれを確認すること。",
            _obj(
                {
                    "payment_term_days": {
                        "type": "integer",
                        "description": "入金遅延とみなす日数。省略時は事務所の設定値。",
                    }
                },
                [],
            ),
            billing_alerts,
        ),
        Tool(
            "billing_overview",
            "全案件の請求状況を横断で集計する。未入金の多い順に返る。"
            "月次の資金繰りや売掛の把握に使う。",
            _obj({}, []),
            billing_overview,
        ),
        Tool(
            "tax_breakdown",
            "税別金額から消費税額と税込金額を計算する。自分で計算しないこと。"
            "税率が事務所プロフィールに未設定なら税込を算出せず、その旨を返す。",
            _obj(
                {"amount": {"type": "integer", "description": "税別金額(円)"}},
                ["amount"],
            ),
            tax_breakdown,
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
        except (WorkspaceError, CompanyError, CompetitorError, InstagramError) as exc:
            return f"エラー: {exc}", True
        except TypeError as exc:
            return f"エラー: 引数が不正です ({exc})", True
        except Exception as exc:  # noqa: BLE001 - 社員は落ちずに報告する
            return f"エラー: {type(exc).__name__}: {exc}", True
        if isinstance(result, str):
            return result, False
        return json.dumps(result, ensure_ascii=False, default=str), False
