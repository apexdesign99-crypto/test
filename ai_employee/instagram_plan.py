"""Instagram の運用計画——月の投稿計画と、その抜けの検出。

単発の投稿を作る仕組み(instagram.py)はあるが、運用に必要なのは
「月の計画を持ち、抜けを見つけ、続ける」こと。ここはその台帳。

    <office>/_company/instagram_plan.json

守らせている制約:
- 掲載許諾のない案件は計画に入れられない
- 必要な素材が揃っていない投稿は「原稿済」にできない
- 投稿済にできるのは、原稿と素材が揃ったものだけ
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import office_root
from .instagram import POST_FORMATS
from .workspace import now

# 投稿の進行状態。
POST_STATUSES = ("企画", "素材待ち", "原稿済", "投稿済", "見送り")

# 月間の型の配分。事務所の運用方針そのもの。
# 施工事例だけを並べても検討初期の層には届かないため、型を混ぜる。
PLAN_MIXES: dict[str, dict[str, Any]] = {
    "standard": {
        "label": "標準(月6本・週1〜2本)",
        "purpose": "事例で信頼を積みつつ、豆知識で検討初期層に届く",
        "mix": {"works": 2, "knowledge": 2, "voice": 1, "staff": 1},
    },
    "light": {
        "label": "軽め(月4本・週1本)",
        "purpose": "続けることを優先する。素材の準備が追いつかない事務所向け",
        "mix": {"works": 2, "knowledge": 2},
    },
    "reach": {
        "label": "認知重視(月8本)",
        "purpose": "保存されやすい豆知識を厚くして、新規に届く量を増やす",
        "mix": {"works": 2, "knowledge": 4, "voice": 1, "staff": 1},
    },
    "event": {
        "label": "イベント月(月7本)",
        "purpose": "見学会・相談会がある月。告知を前後に厚く置く",
        "mix": {"event": 3, "works": 2, "knowledge": 2},
    },
}


class PlanError(RuntimeError):
    """投稿計画の操作に失敗した。"""


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


def month_range(year_month: str) -> tuple[str, str]:
    """"2026-09" から月初と月末の日付を返す。"""
    try:
        year, month = (int(part) for part in year_month.split("-"))
        first = date(year, month, 1)
    except (ValueError, TypeError):
        raise PlanError(f"年月は YYYY-MM の形式で指定してください: {year_month}") from None
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


class InstagramPlan:
    """月の投稿計画。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or office_root()) / "_company"

    @property
    def path(self) -> Path:
        return self.root / "instagram_plan.json"

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, posts: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(posts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------ 計画

    def add(
        self,
        scheduled_date: str,
        post_format: str,
        title: str = "",
        project_id: str = "",
        assignee: str = "",
        note: str = "",
        ledger: Any = None,
        by: str = "",
    ) -> dict[str, Any]:
        """投稿を計画に追加する。

        案件に紐づける場合、掲載許諾がなければ拒否する。
        許諾を取る前に原稿の企画を進めてしまう事故を防ぐため。
        """
        if post_format not in POST_FORMATS:
            raise PlanError(
                f"不正な投稿の型です: {post_format} (選択肢: {'/'.join(POST_FORMATS)})"
            )
        try:
            date.fromisoformat(scheduled_date)
        except ValueError:
            raise PlanError(
                f"予定日は YYYY-MM-DD の形式で指定してください: {scheduled_date}"
            ) from None

        conditions = ""
        if project_id:
            if ledger is None:
                raise PlanError("案件に紐づけるには案件台帳が必要です")
            status = ledger.publication_status(project_id)
            if not status["publishable"]:
                raise PlanError(
                    f"案件「{status['project_name']}」は掲載許諾が"
                    f"「{status['consent_status']}」のため計画に入れられません。"
                    f"{status['guidance']}"
                )
            conditions = status["conditions"]

        post = {
            "id": _short_id(),
            "scheduled_date": scheduled_date,
            "format": post_format,
            "format_label": POST_FORMATS[post_format]["label"],
            "title": title.strip(),
            "project_id": project_id or None,
            "consent_conditions": conditions,
            "assignee": assignee or by or "",
            "status": "企画",
            "assets_ready": False,
            "design_path": "",
            "note": note.strip(),
            "created_at": now().isoformat(timespec="seconds"),
            "created_by": by or "unknown",
        }
        posts = self._read()
        posts.append(post)
        self._write(posts)
        return post

    def update(
        self,
        post_id: str,
        status: str | None = None,
        title: str | None = None,
        assets_ready: bool | None = None,
        design_path: str | None = None,
        scheduled_date: str | None = None,
        assignee: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """計画中の投稿を更新する。

        素材が揃っていないまま「原稿済」「投稿済」にはできない。
        写真がないのに原稿だけ出来ている状態を「準備完了」と扱わせないため。
        """
        if status is not None and status not in POST_STATUSES:
            raise PlanError(
                f"不正な状態です: {status} (選択肢: {'/'.join(POST_STATUSES)})"
            )
        if scheduled_date is not None:
            try:
                date.fromisoformat(scheduled_date)
            except ValueError:
                raise PlanError(f"予定日の形式が不正です: {scheduled_date}") from None

        posts = self._read()
        for post in posts:
            if post["id"] != post_id:
                continue
            ready = post["assets_ready"] if assets_ready is None else assets_ready
            if status in ("原稿済", "投稿済") and not ready:
                needed = "、".join(POST_FORMATS[post["format"]]["assets"])
                raise PlanError(
                    f"素材が揃っていないため「{status}」にできません。"
                    f"この型に必要な素材: {needed}。"
                    f"揃ったら assets_ready を true にしてください。"
                )
            for key, value in (
                ("status", status), ("title", title), ("assets_ready", assets_ready),
                ("design_path", design_path), ("scheduled_date", scheduled_date),
                ("assignee", assignee), ("note", note),
            ):
                if value is not None:
                    post[key] = value.strip() if isinstance(value, str) else value
            post["updated_at"] = now().isoformat(timespec="seconds")
            self._write(posts)
            return post
        raise PlanError(f"投稿が見つかりません: {post_id}")

    def list(
        self, year_month: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """予定日の早い順に返す。"""
        if status is not None and status not in POST_STATUSES:
            raise PlanError(f"不正な状態です: {status}")
        posts = self._read()
        if year_month:
            first, last = month_range(year_month)
            posts = [p for p in posts if first <= p["scheduled_date"] <= last]
        if status:
            posts = [p for p in posts if p["status"] == status]
        posts.sort(key=lambda p: (p["scheduled_date"], p["created_at"]))
        return posts

    def get(self, post_id: str) -> dict[str, Any]:
        for post in self._read():
            if post["id"] == post_id:
                return post
        raise PlanError(f"投稿が見つかりません: {post_id}")

    def delete(self, post_id: str) -> dict[str, Any]:
        posts = self._read()
        for index, post in enumerate(posts):
            if post["id"] == post_id:
                removed = posts.pop(index)
                self._write(posts)
                return removed
        raise PlanError(f"投稿が見つかりません: {post_id}")

    # ------------------------------------------------------------ 下書き

    def draft_month(
        self,
        year_month: str,
        mix: str = "standard",
        assignee: str = "",
        by: str = "",
    ) -> list[dict[str, Any]]:
        """月の計画の骨格を作る。

        型の配分に従って本数を割り振り、月内に均等に並べる。
        中身(題材・原稿・素材)は人と AI社員が埋める。
        既にその月の計画があるときは作らない——上書きで消さないため。
        """
        if mix not in PLAN_MIXES:
            raise PlanError(
                f"不正な配分です: {mix} (選択肢: {'/'.join(PLAN_MIXES)})"
            )
        if self.list(year_month):
            raise PlanError(
                f"{year_month} には既に計画があります。"
                f"作り直す場合は個別に削除してください。"
            )

        first, last = month_range(year_month)
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        slots: list[str] = []
        for post_format, count in PLAN_MIXES[mix]["mix"].items():
            slots.extend([post_format] * count)

        # 月内に均等に配置する。同じ型が続かないよう交互に並べ替える。
        by_format: dict[str, list[str]] = {}
        for post_format in slots:
            by_format.setdefault(post_format, []).append(post_format)
        interleaved: list[str] = []
        while any(by_format.values()):
            for post_format in list(by_format):
                if by_format[post_format]:
                    interleaved.append(by_format[post_format].pop())

        span = (end - start).days
        created = []
        for index, post_format in enumerate(interleaved):
            offset = round(span * (index + 0.5) / len(interleaved))
            created.append(self.add(
                (start + timedelta(days=offset)).isoformat(),
                post_format, assignee=assignee, by=by,
                note=f"{PLAN_MIXES[mix]['label']} の配分から自動作成",
            ))
        return created

    # ------------------------------------------------------------ 点検

    def gaps(self, year_month: str, cadence: int | None = None) -> dict[str, Any]:
        """計画の抜けを洗い出す。

        運用が止まるのは、たいてい「予定日を過ぎたまま放置」か
        「素材待ちのまま動かない」のどちらか。
        """
        posts = self.list(year_month)
        today = now().date().isoformat()

        overdue = [
            p for p in posts
            if p["scheduled_date"] < today and p["status"] not in ("投稿済", "見送り")
        ]
        waiting = [p for p in posts if p["status"] == "素材待ち"]
        no_title = [p for p in posts if p["status"] == "企画" and not p["title"]]
        published = [p for p in posts if p["status"] == "投稿済"]

        planned = len([p for p in posts if p["status"] != "見送り"])
        shortfall = max(0, cadence - planned) if cadence else 0

        return {
            "year_month": year_month,
            "planned": planned,
            "published": len(published),
            "cadence": cadence,
            "shortfall": shortfall,
            "overdue": overdue,
            "waiting_assets": waiting,
            "no_title": no_title,
            "note": "予定日を過ぎた投稿と、素材待ちのまま止まっている投稿が"
            "運用停止の主因。まずここを潰すこと。",
        }
