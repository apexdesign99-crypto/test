"""会社共有の情報——事務所プロフィールと案件台帳。

社員ごとのワークスペースとは別に、事務所全体で 1 つだけ持つ台帳。
集客が拾った反響を営業が追い、設計が図面を起こし、事務が請求する——という
建築設計事務所の流れは、全員が同じ案件を見られないと成立しないため。

    <office>/_company/office.json     事務所プロフィール
    <office>/_company/projects.json   案件台帳
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from datetime import timedelta

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


@dataclass
class OfficeProfile:
    """事務所そのものの情報。

    施主に送る初回返信や案内文を書くには、対応エリア・料金の考え方・
    相談の流れが要る。ここが空のまま書かせると社員が作り話をするため、
    未設定であることを社員に明示して、事務所固有の情報を書かせない。
    """

    name: str = ""
    location: str = ""
    areas: list[str] = field(default_factory=list)
    specialties: list[str] = field(default_factory=list)
    fee_policy: str = ""
    consultation_flow: list[str] = field(default_factory=list)
    business_hours: str = ""
    contact: str = ""
    notes: str = ""

    def is_configured(self) -> bool:
        """施主向けの文面を書くのに足る情報があるか。"""
        return bool(self.name.strip())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OfficeProfile":
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise CompanyError(f"事務所プロフィールに未知の項目があります: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def load(cls, root: Path | None = None) -> "OfficeProfile":
        path = (root or office_root()) / "_company" / "office.json"
        if not path.is_file():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, root: Path | None = None) -> Path:
        path = (root or office_root()) / "_company" / "office.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def as_prompt(self) -> str:
        """system プロンプトに差し込む事務所情報。"""
        if not self.is_configured():
            return (
                "# 事務所情報\n"
                "- 未設定です。事務所名・対応エリア・料金・相談の流れが分かりません。\n"
                "- したがって、施主や社外に向けた文面に事務所固有の情報"
                "(事務所名・エリア・料金・日程・連絡先・実績)を書いてはいけません。\n"
                "- そうした情報が必要な文面を求められたら、"
                "該当箇所を【要記入: 対応エリア】のような差し込み欄として残し、"
                "「事務所プロフィールが未設定である」ことを報告に明記すること。"
            )

        lines = ["# 事務所情報(社外向けの文面はこの範囲で書く)", f"- 事務所名: {self.name}"]
        for label, value in [
            ("所在地", self.location),
            ("料金の考え方", self.fee_policy),
            ("営業時間", self.business_hours),
            ("連絡先", self.contact),
        ]:
            if value.strip():
                lines.append(f"- {label}: {value}")
        for label, values in [
            ("対応エリア", self.areas),
            ("得意分野", self.specialties),
        ]:
            if values:
                lines.append(f"- {label}: {'、'.join(values)}")
        if self.consultation_flow:
            lines.append("- 初回相談の流れ:")
            lines.extend(f"  {i}. {step}" for i, step in enumerate(self.consultation_flow, 1))
        if self.notes.strip():
            lines.append(f"- 補足: {self.notes}")
        lines.append(
            "- ここに書かれていない事務所固有の情報(料金・日程・実績・エリア)は書かない。"
            "必要なら【要確認】として残すこと。"
        )
        return "\n".join(lines)


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

    def stale(self, days: int = 14, stage: str | None = None) -> list[dict[str, Any]]:
        """一定期間動いていない進行中案件を返す。追客漏れの検知に使う。

        「最終更新から days 日以上経過」で判定する。台帳を更新していれば
        接触したことになるので、更新日 = 最終接触日として扱う。
        """
        if days < 0:
            raise CompanyError("日数は 0 以上で指定してください")
        cutoff = (now() - timedelta(days=days)).isoformat(timespec="seconds")
        stalled = [
            project
            for project in self.list(stage=stage, status="active")
            # 「days 日以上動いていない」の素直な読みに合わせて境界を含める。
            # days=0 なら進行中の全案件が対象になる。
            if project["updated_at"] <= cutoff
        ]
        # 放置が長い順(最終更新が古い順)に並べる。
        stalled.sort(key=lambda p: p["updated_at"])
        return stalled

    def by_source(self, since: str | None = None) -> list[dict[str, Any]]:
        """流入経路ごとの反響数と結果。どの施策が効いているかの判断材料。

        受注率は決着済み(受注 + 失注)に対する割合。進行中は母数に含めない。
        """
        buckets: dict[str, dict[str, Any]] = {}
        for project in self._read():
            if since and project["created_at"] < since:
                continue
            source = (project.get("source") or "").strip() or "不明"
            bucket = buckets.setdefault(
                source,
                {"source": source, "total": 0, "active": 0, "won": 0, "lost": 0, "other": 0},
            )
            bucket["total"] += 1
            status = project["status"]
            bucket[status if status in ("active", "won", "lost") else "other"] += 1

        report = []
        for bucket in buckets.values():
            decided = bucket["won"] + bucket["lost"]
            bucket["win_rate"] = (
                round(bucket["won"] / decided * 100, 1) if decided else None
            )
            report.append(bucket)
        report.sort(key=lambda b: b["total"], reverse=True)
        return report

    def pipeline(self) -> dict[str, int]:
        """進行中案件のステージ別件数。営業会議の材料。"""
        counts = {stage: 0 for stage in STAGES}
        for project in self._read():
            if project["status"] == "active":
                counts[project["stage"]] += 1
        return counts
