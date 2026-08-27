"""競合台帳——周辺の住宅会社・工務店・設計事務所の調査記録。

競合の情報を記憶や推測で書くのは、誤情報を社内に残すだけでなく、
他社について事実でないことを書き残すことにもなる。そこでこの台帳は
**出典 URL と調査日のない登録を拒否する。**

坪単価・受注棟数・売上などは、公開されている情報からしか記録できない。
「たぶんこのくらい」は書けない。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .config import office_root
from .workspace import now

# 競合の業態。設計事務所から見た競合はハウスメーカーだけではない。
COMPETITOR_TYPES = ("ハウスメーカー", "ビルダー", "工務店", "設計事務所", "リノベ会社", "その他")

# 訴求軸。どこで戦っているかを比較するための共通のものさし。
APPEAL_AXES = (
    "デザイン性",
    "価格の安さ",
    "高性能(断熱・気密)",
    "自然素材",
    "耐震・構造",
    "施工品質",
    "工期の短さ",
    "アフター・保証",
    "土地探しからの相談",
    "施主との距離・伴走",
    "実績・受賞歴",
    "その他",
)


class CompetitorError(RuntimeError):
    """競合台帳の操作に失敗した。"""


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class CompetitorLedger:
    """事務所で共有する競合の調査記録。

        <office>/_company/competitors.json
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or office_root()) / "_company"

    @property
    def path(self) -> Path:
        return self.root / "competitors.json"

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, records: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------ 登録

    def record(
        self,
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
        by: str = "",
    ) -> dict[str, Any]:
        """競合を登録・更新する。出典がなければ登録できない。"""
        if not name.strip():
            raise CompetitorError("競合名は必須です")
        if not area.strip():
            raise CompetitorError("対象エリアは必須です")

        cleaned_sources = [s.strip() for s in (sources or []) if s.strip()]
        if not cleaned_sources:
            raise CompetitorError(
                "出典(調べた情報源の URL)が必須です。"
                "記憶や推測で競合の情報を登録してはいけません。"
                "公開情報を確認し、その URL を渡してください。"
            )
        if company_type not in COMPETITOR_TYPES:
            raise CompetitorError(
                f"不正な業態です: {company_type} (選択肢: {'/'.join(COMPETITOR_TYPES)})"
            )
        axes = [a.strip() for a in (appeal_axes or []) if a.strip()]
        unknown = set(axes) - set(APPEAL_AXES)
        if unknown:
            raise CompetitorError(
                f"不正な訴求軸です: {sorted(unknown)} (選択肢: {'/'.join(APPEAL_AXES)})"
            )
        if followers is not None and followers < 0:
            raise CompetitorError("フォロワー数は 0 以上で指定してください")

        stamp = now().isoformat(timespec="seconds")
        records = self._read()
        existing = next(
            (r for r in records if r["name"] == name.strip() and r["area"] == area.strip()),
            None,
        )
        payload = {
            "name": name.strip(),
            "area": area.strip(),
            "type": company_type,
            "appeal_axes": axes,
            "price_range": price_range.strip(),
            "instagram": instagram.strip(),
            "followers": followers,
            "post_frequency": post_frequency.strip(),
            "strengths": strengths.strip(),
            "note": note.strip(),
            "sources": cleaned_sources,
            "researched_at": stamp,
            "researched_by": by or "unknown",
        }
        if existing:
            existing.update(payload)
            record = existing
        else:
            record = {"id": _short_id(), **payload}
            records.append(record)
        self._write(records)
        return record

    def list(
        self, area: str | None = None, company_type: str | None = None
    ) -> list[dict[str, Any]]:
        """調査済みの競合を、新しく調べた順に返す。"""
        if company_type is not None and company_type not in COMPETITOR_TYPES:
            raise CompetitorError(f"不正な業態です: {company_type}")
        needle = (area or "").strip()
        hits = [
            r
            for r in self._read()
            if (not needle or needle in r["area"])
            and (not company_type or r["type"] == company_type)
        ]
        hits.sort(key=lambda r: r["researched_at"], reverse=True)
        return hits

    def get(self, competitor_id: str) -> dict[str, Any]:
        for record in self._read():
            if record["id"] == competitor_id:
                return record
        raise CompetitorError(f"競合が見つかりません: {competitor_id}")

    def delete(self, competitor_id: str) -> dict[str, Any]:
        records = self._read()
        for index, record in enumerate(records):
            if record["id"] == competitor_id:
                removed = records.pop(index)
                self._write(records)
                return removed
        raise CompetitorError(f"競合が見つかりません: {competitor_id}")

    # ------------------------------------------------------------ 分析

    def appeal_report(
        self, area: str | None = None, own_axes: list[str] | None = None
    ) -> dict[str, Any]:
        """訴求軸ごとに何社が言っているかを集計し、空いている軸を出す。

        「誰も言っていない軸」は、そこに需要があることを意味しない。
        需要がないから誰も言っていない可能性もある。判断材料であって答えではない。
        """
        competitors = self.list(area=area)
        counts = {axis: 0 for axis in APPEAL_AXES}
        for record in competitors:
            for axis in record["appeal_axes"]:
                counts[axis] += 1

        crowded = sorted(
            ((axis, n) for axis, n in counts.items() if n),
            key=lambda pair: -pair[1],
        )
        empty = [axis for axis, n in counts.items() if n == 0 and axis != "その他"]

        own = [a for a in (own_axes or []) if a in APPEAL_AXES]
        differentiators = [axis for axis in own if counts[axis] == 0]
        contested = [(axis, counts[axis]) for axis in own if counts[axis] > 0]

        return {
            "area": area,
            "competitor_count": len(competitors),
            "counts": counts,
            "crowded_axes": crowded,
            "empty_axes": empty,
            "own_axes": own,
            "differentiators": differentiators,
            "contested_axes": contested,
            "caveat": "調査できた競合の範囲での集計であり、市場全体ではない。"
            "誰も言っていない軸は、需要がないから空いている可能性もある。"
            "この集計は判断材料であって結論ではない。",
        }
