"""会社共有の案件台帳。

社員ごとのワークスペースとは別に、事務所全体で 1 つだけ持つ台帳。
集客が拾った反響を営業が追い、設計が図面を起こし、事務が請求する——という
建築設計事務所の流れは、全員が同じ案件を見られないと成立しないため。

    <office>/_company/projects.json
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .config import office_root
from .workspace import now

# 設計事務所の標準的な案件ステージ。事務所ごとの実態に合わせて編集してよい。
STAGES = (
    "反響",
    "初回相談",
    "現地調査",
    "プラン提案",
    "見積",
    "設計契約",
    "基本設計",
    "実施設計",
    "確認申請",
    "着工",
    "監理",
    "竣工",
    "アフター",
)

# 案件の生死。ステージ(どこまで進んだか)とは別軸で管理する。
STATUSES = ("active", "won", "lost", "onhold", "done")

# 用途種別。
KINDS = ("戸建住宅", "共同住宅", "店舗", "オフィス", "医療福祉", "公共", "改修", "その他")


class CompanyError(RuntimeError):
    """案件台帳の操作に失敗した。"""


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class ProjectLedger:
    """事務所全体で共有する案件台帳。

    読み込み→更新→書き戻しの単純な方式なので、複数の社員を同時並行で
    走らせる場合は 1 案件への同時更新を避けること。
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or office_root()) / "_company"

    @property
    def path(self) -> Path:
        return self.root / "projects.json"

    # ------------------------------------------------------------ 入出力

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, projects: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(projects, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # -------------------------------------------------------------- 操作

    def add(
        self,
        name: str,
        client: str = "",
        kind: str = "その他",
        stage: str = "反響",
        source: str = "",
        site: str = "",
        budget: str = "",
        owner: str = "",
        by: str = "",
    ) -> dict[str, Any]:
        """案件を起こす。反響が入った時点で登録する想定。"""
        if not name.strip():
            raise CompanyError("案件名は必須です")
        if stage not in STAGES:
            raise CompanyError(f"不正なステージです: {stage} (選択肢: {'/'.join(STAGES)})")
        if kind not in KINDS:
            raise CompanyError(f"不正な用途種別です: {kind} (選択肢: {'/'.join(KINDS)})")

        stamp = now().isoformat(timespec="seconds")
        project = {
            "id": _short_id(),
            "name": name.strip(),
            "client": client.strip(),
            "kind": kind,
            "stage": stage,
            "status": "active",
            "source": source.strip(),
            "site": site.strip(),
            "budget": budget.strip(),
            "owner": owner.strip(),
            "next_action": "",
            "next_due": None,
            "created_at": stamp,
            "updated_at": stamp,
            "history": [
                {"at": stamp, "by": by or owner or "system", "entry": "案件を登録した"}
            ],
        }
        projects = self._read()
        projects.append(project)
        self._write(projects)
        return project

    def get(self, project_id: str) -> dict[str, Any]:
        for project in self._read():
            if project["id"] == project_id:
                return project
        raise CompanyError(f"案件が見つかりません: {project_id}")

    def list(
        self,
        stage: str | None = None,
        status: str | None = "active",
        owner: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """案件を絞り込む。既定では進行中のものだけを返す。"""
        if status not in (None, "all", *STATUSES):
            raise CompanyError(f"不正なステータスです: {status}")
        if stage is not None and stage not in STAGES:
            raise CompanyError(f"不正なステージです: {stage}")

        needle = (query or "").strip().lower()
        hits = []
        for project in self._read():
            if status not in (None, "all") and project["status"] != status:
                continue
            if stage and project["stage"] != stage:
                continue
            if owner and project.get("owner") != owner:
                continue
            if needle:
                haystack = " ".join(
                    str(project.get(f, "")) for f in ("name", "client", "site", "source")
                ).lower()
                if needle not in haystack:
                    continue
            hits.append(project)
        hits.sort(key=lambda p: (p["next_due"] or "9999", p["updated_at"]))
        return hits

    def update(
        self,
        project_id: str,
        note: str,
        by: str = "",
        **fields: Any,
    ) -> dict[str, Any]:
        """案件を更新する。何をなぜ変えたかの `note` を必ず履歴に残す。"""
        if not note.strip():
            raise CompanyError("更新理由 (note) は必須です")

        editable = {
            "client",
            "kind",
            "stage",
            "status",
            "source",
            "site",
            "budget",
            "owner",
            "next_action",
            "next_due",
            "name",
        }
        changes = {k: v for k, v in fields.items() if v is not None}
        unknown = set(changes) - editable
        if unknown:
            raise CompanyError(f"更新できない項目です: {sorted(unknown)}")
        if "stage" in changes and changes["stage"] not in STAGES:
            raise CompanyError(f"不正なステージです: {changes['stage']}")
        if "status" in changes and changes["status"] not in STATUSES:
            raise CompanyError(f"不正なステータスです: {changes['status']}")
        if "kind" in changes and changes["kind"] not in KINDS:
            raise CompanyError(f"不正な用途種別です: {changes['kind']}")

        projects = self._read()
        for project in projects:
            if project["id"] != project_id:
                continue
            before = {k: project.get(k) for k in changes}
            project.update(changes)
            stamp = now().isoformat(timespec="seconds")
            project["updated_at"] = stamp
            diff = ", ".join(
                f"{k}: {before[k] or '(空)'} → {v}" for k, v in changes.items()
            )
            project["history"].append(
                {
                    "at": stamp,
                    "by": by or "unknown",
                    "entry": note.strip() + (f" [{diff}]" if diff else ""),
                }
            )
            self._write(projects)
            return project
        raise CompanyError(f"案件が見つかりません: {project_id}")

    def log(self, project_id: str, entry: str, by: str = "") -> dict[str, Any]:
        """案件の履歴に出来事を 1 行追記する(項目は変えない)。"""
        if not entry.strip():
            raise CompanyError("履歴の内容は必須です")
        return self.update(project_id, note=entry, by=by)

    # ------------------------------------------------------------ 集計

    def pipeline(self) -> dict[str, int]:
        """進行中案件のステージ別件数。営業会議の材料。"""
        counts = {stage: 0 for stage in STAGES}
        for project in self._read():
            if project["status"] == "active":
                counts[project["stage"]] += 1
        return counts
