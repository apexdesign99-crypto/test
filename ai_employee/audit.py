"""社内点検——事務所のデータと社員の運用を機械的に検査する。

セキュリティ担当が使う。**「安全である」ことは証明できない**ので、
このモジュールがやるのは「既知のパターンに当てはまるものを挙げる」ことだけ。
指摘がゼロでも問題がないとは限らない。

点検する範囲:
- 資格情報の扱い(期限・パーミッション・他ファイルへの漏洩)
- 個人情報の置き場所(本来置くべきでない場所に入っていないか)
- 掲載許諾の整合(許諾なしの発信、条件の欠落)
- 社員の権限(誰が何を持っているか)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .company import CONSENT_STATUSES, OfficeProfile, ProjectLedger
from .competitor import CompetitorLedger
from .config import office_root
from .instagram_api import credentials_path, load_credentials
from .instagram_plan import InstagramPlan
from .workspace import Workspace, roster

# 重大度。高 = 実害が出うる / 中 = 要確認 / 低 = 把握のため。
SEVERITIES = ("高", "中", "低")

# 個人が特定されうる記述。copycheck と同じ考え方だが、
# こちらは「本来その場所に無いはずのもの」を探す。
_PERSON = re.compile(r"[一-鿿]{1,4}(?:様|さん)邸|[一-鿿]{2,4}邸")
_ADDRESS = re.compile(
    r"\d{1,3}\s*丁目\s*\d{1,3}(?:\s*[-−]\s*\d{1,3})?"
    r"|\d{1,3}[-−]\d{1,3}[-−]\d{1,4}\s*(?:番地)?"
)
_PHONE = re.compile(r"0\d{1,4}[-(]\d{1,4}[-)]\d{3,4}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# 点検で読むファイルの上限。巨大なファイルで止まらないように。
MAX_SCAN_BYTES = 512 * 1024


@dataclass
class Finding:
    """指摘 1 件。"""

    severity: str
    category: str
    where: str
    detail: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity, "category": self.category,
            "where": self.where, "detail": self.detail, "action": self.action,
        }


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def add(self, severity: str, category: str, where: str, detail: str, action: str) -> None:
        self.findings.append(Finding(severity, category, where, detail, action))

    def to_dict(self) -> dict[str, Any]:
        order = {level: index for index, level in enumerate(SEVERITIES)}
        findings = sorted(self.findings, key=lambda f: (order[f.severity], f.category))
        counts = {level: 0 for level in SEVERITIES}
        for finding in findings:
            counts[finding.severity] += 1
        return {
            "count": len(findings),
            "by_severity": counts,
            "findings": [f.to_dict() for f in findings],
            "checked": self.checked,
            "disclaimer": "既知のパターンによる機械的な点検であり、"
            "安全性の保証ではない。指摘がゼロでも問題がないとは限らない。"
            "見落としも誤検出もあるため、最終的な判断は人が行うこと。",
        }


def _read_text(path: Path) -> str:
    """点検用にファイルを読む。読めなければ空文字を返す。"""
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return path.read_bytes()[:MAX_SCAN_BYTES].decode("utf-8", errors="replace")
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 資格情報


def check_credentials(root: Path, report: AuditReport) -> None:
    report.checked.append("資格情報の期限とパーミッション")
    path = credentials_path(root)
    credentials = load_credentials(root)
    if credentials is None:
        return

    if path.is_file():
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            report.add(
                "高", "資格情報", str(path),
                f"アクセストークンのファイルが他者から読める状態です(現在 {oct(mode)[2:]})。",
                "chmod 600 で所有者のみに制限してください。",
            )

    left = credentials.days_left()
    if credentials.is_expired():
        report.add(
            "中", "資格情報", "Instagram",
            "アクセストークンの有効期限が切れています。",
            "期限切れは更新できません。docs/instagram-setup.md の手順で取り直してください。",
        )
    elif left is not None and credentials.needs_refresh():
        report.add(
            "中", "資格情報", "Instagram",
            f"アクセストークンの残りが {left} 日です。",
            "python -m ai_employee instagram --refresh で更新してください。",
        )

    # トークンが他のファイルに混入していないか。ここが最も実害が大きい。
    report.checked.append("トークンの他ファイルへの混入")
    token = credentials.access_token
    if len(token) < 12:
        return
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_file() or candidate == path:
            continue
        if token in _read_text(candidate):
            report.add(
                "高", "資格情報", str(candidate.relative_to(root)),
                "アクセストークンがこのファイルに含まれています。",
                "該当箇所を削除してください。既に共有した可能性があるなら、"
                "トークンを失効させて取り直してください。",
            )


# ---------------------------------------------------------------- 個人情報


def _pii_hits(text: str) -> list[str]:
    hits = []
    for label, pattern in (("氏名", _PERSON), ("番地", _ADDRESS),
                           ("電話番号", _PHONE), ("メールアドレス", _EMAIL)):
        for match in pattern.finditer(text):
            hits.append(f"{label}: {match.group()}")
    return hits[:5]


def check_pii_placement(root: Path, report: AuditReport) -> None:
    """本来置くべきでない場所に個人情報が入っていないか。

    案件台帳と業務メモには入っていて当然なので見ない。
    """
    report.checked.append("個人情報の置き場所(競合台帳・成果物・投稿計画)")

    for record in CompetitorLedger(root).list():
        blob = " ".join(str(record.get(key, "")) for key in
                        ("name", "area", "strengths", "note", "price_range"))
        hits = _pii_hits(blob)
        if hits:
            report.add(
                "中", "個人情報", f"競合台帳 [{record['id']}] {record['name']}",
                "他社の調査記録に個人が特定されうる記述があります: " + "、".join(hits),
                "競合台帳は他社の公開情報を置く場所です。"
                "施主に関する記述であれば案件台帳へ移してください。",
            )

    for post in InstagramPlan(root).list():
        hits = _pii_hits(f"{post.get('title', '')} {post.get('note', '')}")
        if hits:
            report.add(
                "中", "個人情報", f"投稿計画 [{post['id']}] {post['scheduled_date']}",
                "投稿の題材に個人が特定されうる記述があります: " + "、".join(hits),
                "掲載許諾の条件を確認し、必要なら「S様邸」等に伏せてください。",
            )

    for profile in roster(root):
        workspace = Workspace(profile.employee_id, root)
        for relative in workspace.list_files():
            hits = _pii_hits(_read_text(workspace.resolve(relative)))
            if hits:
                report.add(
                    "中", "個人情報", f"{profile.employee_id}/files/{relative}",
                    "成果物ファイルに個人が特定されうる記述があります: " + "、".join(hits),
                    "社外に渡すファイルであれば、掲載許諾と伏せ字の要否を確認してください。",
                )


# ---------------------------------------------------------------- 掲載許諾


def check_consent(root: Path, report: AuditReport) -> None:
    report.checked.append("掲載許諾と発信記録の整合")
    ledger = ProjectLedger(root)

    for project in ledger.list(status="all"):
        consent = project.get("consent") or {}
        status = consent.get("status", "未確認")
        publications = project.get("publications", [])

        if publications and status not in ("許諾済", "条件付き"):
            report.add(
                "高", "掲載許諾", f"[{project['id']}] {project['name']}",
                f"掲載許諾が「{status}」なのに発信記録が {len(publications)} 件あります。"
                + "、".join(f"{p['channel']}: {p['title']}" for p in publications[:3]),
                "許諾を確認できないなら、公開済みの内容を取り下げるか、"
                "施主に許諾を取ってください。",
            )
        if status == "条件付き" and not (consent.get("conditions") or "").strip():
            report.add(
                "中", "掲載許諾", f"[{project['id']}] {project['name']}",
                "「条件付き」ですが条件が記録されていません。",
                "何が許されているのか分からない状態です。施主に確認して記録してください。",
            )
        if status not in CONSENT_STATUSES:
            report.add(
                "中", "掲載許諾", f"[{project['id']}] {project['name']}",
                f"許諾の状態が不正です: {status}",
                f"{'/'.join(CONSENT_STATUSES)} のいずれかに直してください。",
            )


# ---------------------------------------------------------------- 権限


def check_permissions(root: Path, report: AuditReport) -> None:
    report.checked.append("社員の権限(Web検索・ツール)")
    from .profile import DEFAULT_TOOLS

    for profile in roster(root):
        if profile.web_access:
            report.add(
                "低", "権限", f"{profile.employee_id}({profile.name})",
                f"{profile.role} は Web 検索の権限を持っています。",
                "外部から読み込んだ内容には指示文が紛れうるため、"
                "この社員の報告は特に出典を確認してください。",
            )
        extra = sorted(set(profile.tools) - set(DEFAULT_TOOLS))
        if extra:
            report.add(
                "低", "権限", f"{profile.employee_id}({profile.name})",
                f"既定にないツールを持っています: {'、'.join(extra)}",
                "意図した付与か確認してください。",
            )


# ---------------------------------------------------------------- 台帳


def check_integrity(root: Path, report: AuditReport) -> None:
    report.checked.append("台帳ファイルの健全性")
    company = root / "_company"
    if not company.is_dir():
        return
    for path in sorted(company.glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            report.add(
                "高", "台帳", str(path.relative_to(root)),
                f"ファイルが壊れていて読み込めません: {exc}",
                "バックアップから復元するか、手で修正してください。"
                "壊れたままだと画面も社員も動きません。",
            )


# ---------------------------------------------------------------- 実行


CHECKS = {
    # 台帳の健全性を最初に見る。壊れていれば他の点検も不完全になるため。
    "integrity": ("台帳", check_integrity),
    "credentials": ("資格情報", check_credentials),
    "pii": ("個人情報", check_pii_placement),
    "consent": ("掲載許諾", check_consent),
    "permissions": ("権限", check_permissions),
}


def audit(root: Path | None = None, only: str | None = None) -> dict[str, Any]:
    """事務所のデータを点検する。"""
    base = root or office_root()
    if only is not None and only not in CHECKS:
        raise ValueError(
            f"不正な点検項目です: {only} (選択肢: {'/'.join(CHECKS)})"
        )
    report = AuditReport()
    for key, (label, check) in CHECKS.items():
        if only and key != only:
            continue
        try:
            check(base, report)
        except Exception as exc:  # noqa: BLE001 - 1項目の失敗で点検全体を止めない
            # 台帳が壊れているときこそ点検が要る。他の項目は続ける。
            report.add(
                "高", "点検", label,
                f"この項目を点検できませんでした: {type(exc).__name__}: {exc}",
                "データが壊れている可能性があります。"
                "python -m ai_employee doctor で状態を確認してください。"
                "点検できていない範囲があることに注意してください。",
            )
    return report.to_dict()
