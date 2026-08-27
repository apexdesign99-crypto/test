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

# 1 坪 = 400/121 ㎡。
TSUBO_SQM = 400 / 121


# 初回相談で必ず押さえる項目。ここが埋まらないまま提案に進むと必ず手戻りする。
# (key, 表示名, 提案前に必須か)
HEARING_ITEMS: tuple[tuple[str, str, bool], ...] = (
    ("budget", "予算(総額)", True),
    ("funding", "資金計画(自己資金・借入)", True),
    ("land", "土地の状況(所有/取得済/検討中/未定)", True),
    ("move_in", "入居・開業の希望時期", True),
    ("floor_area", "希望延床面積", True),
    ("family", "家族構成・利用人数", False),
    ("parking", "駐車台数", False),
    ("priorities", "要望の優先順位", True),
    ("decision_maker", "決裁者", True),
    ("competitors", "他社の検討状況", False),
)

HEARING_KEYS = tuple(key for key, _, _ in HEARING_ITEMS)
HEARING_LABELS = {key: label for key, label, _ in HEARING_ITEMS}
HEARING_REQUIRED = tuple(key for key, _, required in HEARING_ITEMS if required)


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

    # 概算算定用。AI に坪単価や料率を推測させないため、事務所が明示的に設定する。
    # unit_prices: 用途種別 -> [下限, 上限] 万円/坪
    unit_prices: dict[str, list[int]] = field(default_factory=dict)
    design_fee_rate: float | None = None      # 設計監理料率 (%)
    design_fee_minimum: int | None = None     # 設計監理料の最低額 (万円)

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

    def can_estimate(self, kind: str) -> bool:
        return bool(self.unit_prices.get(kind)) and self.design_fee_rate is not None

    def estimate(
        self,
        kind: str,
        floor_area_tsubo: float | None = None,
        floor_area_sqm: float | None = None,
    ) -> dict[str, Any]:
        """延床面積から工事費と設計監理料の概算レンジを出す。

        計算はここで行い、社員には結果と根拠だけを渡す。暗算をさせない。
        単価や料率が未設定なら概算を出さずに失敗させる——推測させないため。
        """
        if kind not in KINDS:
            raise CompanyError(f"不正な用途種別です: {kind} (選択肢: {'/'.join(KINDS)})")
        if (floor_area_tsubo is None) == (floor_area_sqm is None):
            raise CompanyError("延床面積は 坪 か ㎡ のどちらか一方を指定してください")

        tsubo = floor_area_tsubo if floor_area_tsubo is not None else floor_area_sqm / TSUBO_SQM
        if tsubo <= 0:
            raise CompanyError("延床面積は 0 より大きい値で指定してください")

        prices = self.unit_prices.get(kind)
        if not prices:
            raise CompanyError(
                f"「{kind}」の坪単価が事務所プロフィールに未設定のため、概算を出せません。"
                f"office コマンドの --unit-prices で設定してください。"
                f"設定されるまで概算金額を書いてはいけません。"
            )
        if self.design_fee_rate is None:
            raise CompanyError(
                "設計監理料率が事務所プロフィールに未設定のため、設計料を算定できません。"
                "office コマンドの --design-fee-rate で設定してください。"
            )

        low_unit, high_unit = prices
        # int で渡されても "35 坪" / "35.0 坪" と揺れないよう float に揃える。
        tsubo = round(float(tsubo), 1)
        construction = {
            "low": round(low_unit * tsubo),
            "high": round(high_unit * tsubo),
        }
        rate = self.design_fee_rate
        raw_fee = {
            "low": round(construction["low"] * rate / 100),
            "high": round(construction["high"] * rate / 100),
        }
        minimum = self.design_fee_minimum
        fee = dict(raw_fee)
        applied_minimum = False
        if minimum is not None:
            if fee["low"] < minimum:
                fee["low"] = minimum
                applied_minimum = True
            if fee["high"] < minimum:
                fee["high"] = minimum
                applied_minimum = True

        basis = (
            f"延床 {tsubo} 坪 × 坪単価 {low_unit}〜{high_unit} 万円 = "
            f"工事費 {construction['low']:,}〜{construction['high']:,} 万円。"
            f"設計監理料は工事費の {rate}% で {raw_fee['low']:,}〜{raw_fee['high']:,} 万円"
        )
        if applied_minimum:
            basis += f"(最低額 {minimum:,} 万円を適用)"
        basis += "。単位はすべて万円・税別。"

        return {
            "kind": kind,
            "floor_area_tsubo": tsubo,
            "floor_area_sqm": round(tsubo * TSUBO_SQM, 1),
            "unit_price_range": [low_unit, high_unit],
            "construction_cost": construction,
            "design_fee": {
                **fee,
                "rate_percent": rate,
                "minimum": minimum,
                "applied_minimum": applied_minimum,
            },
            "basis": basis,
            "caveat": "坪単価に基づく概算であり、確定金額ではない。"
            "地盤・外構・別途工事・設備グレードで変動する。提示時は必ず前提と"
            "確定でない旨を併記すること。",
        }

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
        if self.unit_prices and self.design_fee_rate is not None:
            ranges = "、".join(
                f"{kind} {low}〜{high} 万円/坪" for kind, (low, high) in self.unit_prices.items()
            )
            lines.append(f"- 概算の坪単価: {ranges}")
            lines.append(
                f"- 設計監理料率: {self.design_fee_rate}%"
                + (f"(最低 {self.design_fee_minimum:,} 万円)" if self.design_fee_minimum else "")
            )
            lines.append(
                "- 概算金額は estimate_cost ツールで算定する。自分で掛け算をしない。"
            )
        else:
            lines.append(
                "- 坪単価・設計監理料率が未設定のため、概算金額を出せない。"
                "金額を求められたら算定できない旨と、必要な設定を報告すること。"
                "推測した数字を書いてはいけない。"
            )
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
            # ヒアリング結果。未記入の項目は「未確認」であることが機械的に分かる。
            "requirements": {},
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

    def record_hearing(
        self, project_id: str, items: dict[str, str], by: str = ""
    ) -> dict[str, Any]:
        """ヒアリング結果を案件に記録する(部分更新)。

        聞けた項目だけを渡せばよい。渡さなかった項目は「未確認」のまま残り、
        hearing_gaps で機械的に拾える。営業の最大の失敗は聞き漏れなので、
        埋まっているかどうかを社員の自己申告に任せない。
        """
        unknown = set(items) - set(HEARING_KEYS)
        if unknown:
            raise CompanyError(
                f"ヒアリング項目として未知です: {sorted(unknown)} "
                f"(有効な項目: {', '.join(HEARING_KEYS)})"
            )
        recorded = {k: str(v).strip() for k, v in items.items() if str(v).strip()}
        if not recorded:
            raise CompanyError("記録する内容がありません")

        projects = self._read()
        for project in projects:
            if project["id"] != project_id:
                continue
            requirements = dict(project.get("requirements") or {})
            requirements.update(recorded)
            project["requirements"] = requirements
            stamp = now().isoformat(timespec="seconds")
            project["updated_at"] = stamp
            project["history"].append(
                {
                    "at": stamp,
                    "by": by or "unknown",
                    "entry": "ヒアリングを記録: "
                    + "、".join(HEARING_LABELS[k] for k in recorded),
                }
            )
            self._write(projects)
            return project
        raise CompanyError(f"案件が見つかりません: {project_id}")

    def hearing_gaps(self, project_id: str) -> dict[str, Any]:
        """まだ聞けていない項目を返す。提案に進む前の関門。"""
        project = self.get(project_id)
        requirements = project.get("requirements") or {}

        def entry(key: str) -> dict[str, str]:
            return {"key": key, "label": HEARING_LABELS[key]}

        missing = [entry(k) for k in HEARING_KEYS if not requirements.get(k)]
        missing_required = [
            entry(k) for k in HEARING_REQUIRED if not requirements.get(k)
        ]
        return {
            "project_id": project["id"],
            "project_name": project["name"],
            "recorded": {HEARING_LABELS[k]: v for k, v in requirements.items()},
            "missing": missing,
            "missing_required": missing_required,
            "ready_for_proposal": not missing_required,
        }

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
