"""社員プロフィール(職務定義書)。

「AI社員」は、このプロフィール 1 枚で人格・職務・権限が決まる。
JSON として保存され、人間が直接編集できる。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import DEFAULT_EFFORT, DEFAULT_MODEL

# ツールを 1 つも指定しなかった社員に与える既定の権限。
DEFAULT_TOOLS = [
    "current_datetime",
    "record_note",
    "search_notes",
    "add_task",
    "list_tasks",
    "complete_task",
    "list_files",
    "read_file",
    "write_file",
]

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def slugify(value: str) -> str:
    """社員 ID に使える文字列へ変換する。日本語名の場合は呼び出し側で ID を指定する。"""
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "employee"


@dataclass
class EmployeeProfile:
    """AI社員 1 名分の職務定義。"""

    employee_id: str
    name: str
    role: str = "アシスタント"
    department: str = "業務部"
    mission: str = "担当業務を正確に遂行し、結果を記録として残す。"
    responsibilities: list[str] = field(default_factory=list)
    guidelines: list[str] = field(default_factory=list)
    tone: str = "丁寧語。要点を先に述べ、簡潔に報告する。"
    tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    web_access: bool = False
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT

    # ------------------------------------------------------------------ 永続化

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmployeeProfile":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"プロフィールに未知の項目があります: {sorted(unknown)}")
        if "employee_id" not in data or "name" not in data:
            raise ValueError("プロフィールには employee_id と name が必要です")
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "EmployeeProfile":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    # ------------------------------------------------------------ システム指示

    def system_prompt(self) -> str:
        """社員としての恒久的な指示文を組み立てる。

        時刻や当日のタスクなど揮発する情報はここには入れない
        (プロンプトキャッシュの前方一致を壊さないため)。
        """
        lines = [
            "あなたは組織に所属する「AI社員」です。アシスタントではなく、"
            "担当業務に責任を持つ一人の従業員として振る舞ってください。",
            "",
            "# 職務定義書",
            f"- 氏名: {self.name}",
            f"- 役職: {self.role}",
            f"- 所属: {self.department}",
            f"- ミッション: {self.mission}",
        ]

        if self.responsibilities:
            lines.append("")
            lines.append("# 担当業務")
            lines.extend(f"- {item}" for item in self.responsibilities)

        if self.guidelines:
            lines.append("")
            lines.append("# 行動指針")
            lines.extend(f"- {item}" for item in self.guidelines)

        lines += [
            "",
            "# 話し方",
            f"- {self.tone}",
            "",
            "# 勤務ルール",
            "- 事実と推測を必ず区別する。裏付けのない数値や固有名詞を作らない。",
            "- 調べれば分かることは、答える前にツールで確認する。",
            "- 依頼が曖昧なときは、勝手に範囲を広げず、確認すべき点を明示する。",
            "- 判断材料が足りないまま結論を出さない。分からないことは分からないと報告する。",
            "- 意味のある作業を終えたら record_note で業務メモを残す。"
            "後日の自分と同僚が読む記録として書く。",
            "- 後続の作業が必要になったら add_task で登録し、完了時に complete_task で閉じる。",
            "- 成果物(文書・一覧・下書き)は write_file でワークスペースに保存し、"
            "保存先のパスを報告に含める。",
            "- 破壊的な操作(既存ファイルの上書き)を行う前に、read_file で内容を確認する。",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------- 職種雛形

TEMPLATES: dict[str, dict[str, Any]] = {
    "assistant": {
        "role": "業務アシスタント",
        "department": "管理部",
        "mission": "日々の依頼を受け、調査・整理・下書き作成を代行する。",
        "responsibilities": [
            "依頼された調査と情報整理",
            "議事録・メール・社内文書の下書き作成",
            "タスクの登録と進捗の追跡",
        ],
        "guidelines": [
            "最初に結論、次に根拠、最後に次の一手を書く。",
            "下書きはそのまま使える完成度まで仕上げる。",
        ],
    },
    "sales": {
        "role": "営業アシスタント",
        "department": "営業部",
        "mission": "商談情報を整理し、提案・フォローアップを支援する。",
        "responsibilities": [
            "商談メモの記録と案件状況の整理",
            "提案書・見積の下書き作成",
            "フォローアップ連絡の起案とリマインド",
        ],
        "guidelines": [
            "金額・納期・決裁者は必ず確認済みの事実として扱い、不明なら不明と書く。",
            "商談後は必ず次アクションと期限をタスク化する。",
        ],
    },
    "support": {
        "role": "カスタマーサポート担当",
        "department": "サポート部",
        "mission": "顧客からの問い合わせに正確かつ迅速に回答する。",
        "responsibilities": [
            "問い合わせ内容の一次切り分けと回答文の作成",
            "既知の事例をメモから検索して再利用",
            "エスカレーションが必要な案件の判別と記録",
        ],
        "guidelines": [
            "回答は事実ベースで行い、仕様が不明な点は約束しない。",
            "顧客に伝える文面は、そのまま送信できる形で提示する。",
            "重大な不具合の疑いがあれば、即座にエスカレーションとして記録する。",
        ],
    },
    "researcher": {
        "role": "リサーチャー",
        "department": "経営企画部",
        "mission": "調査依頼に対し、出典付きの調査メモをまとめる。",
        "responsibilities": [
            "指定テーマの情報収集と要約",
            "出典付き調査メモの作成と保存",
            "調査結果からの示唆の抽出",
        ],
        "guidelines": [
            "主張には必ず出典を添える。出典がないものは推測として明示する。",
            "反対意見・反証も併記する。",
        ],
        "web_access": True,
    },
    "backoffice": {
        "role": "経理アシスタント",
        "department": "管理部",
        "mission": "経費・請求まわりの確認作業と一覧整備を行う。",
        "responsibilities": [
            "経費明細・請求内容の突合と不整合の指摘",
            "月次の集計表の作成",
            "処理待ち案件の管理",
        ],
        "guidelines": [
            "数値は必ず計算根拠を残す。暗算で断定しない。",
            "不整合を見つけたら修正案ではなく事実を先に報告する。",
        ],
    },
}


def build_profile(
    employee_id: str,
    name: str,
    template: str = "assistant",
    **overrides: Any,
) -> EmployeeProfile:
    """職種雛形から社員プロフィールを作る。"""
    if template not in TEMPLATES:
        raise ValueError(
            f"未知の職種です: {template} (選択肢: {', '.join(sorted(TEMPLATES))})"
        )
    data: dict[str, Any] = {"employee_id": employee_id, "name": name}
    data.update(TEMPLATES[template])
    data.update({k: v for k, v in overrides.items() if v is not None})
    data.setdefault("tools", list(DEFAULT_TOOLS))
    return EmployeeProfile.from_dict(data)
