"""原稿の表現チェック。

施主に届く原稿で事故になりやすい表現を、決まったパターンで機械的に拾う。
AI に「問題ないと思います」と自己申告させるより、同じ基準で毎回引っかける方が信頼できる。

**これは法的な適法判断ではない。** 拾えるのは既知のパターンだけで、
見落としも誤検出もある。最終的な掲載可否は必ず人が判断すること。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# (カテゴリ, 正規表現, 指摘理由)
_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "最上級表現",
        r"(?:No\.?\s*1|ナンバーワン|ナンバー1|日本一|世界一|業界一)",
        "客観的な裏付けと出典がなければ使えない。順位を示す調査名・時点・範囲を添えるか、削除する。",
    ),
    (
        "最上級表現",
        r"(?:最高|最安|最良|最も安い|最も優れ|一番安い|業界最[高安大])",
        "比較の範囲と根拠が示せなければ使えない。具体的な事実に言い換える。",
    ),
    (
        "唯一性の主張",
        r"(?:唯一|他にはない|当社だけ|業界初|日本初|世界初)",
        "「初」「唯一」は調査範囲と時点の裏付けが必要。裏が取れないなら削除する。",
    ),
    (
        "断定・保証",
        r"(?:必ず|絶対に|絶対|100[%％]|確実に|保証します|間違いなく)",
        "結果を保証する表現。実現できない場合に問題になる。条件付きの表現に改める。",
    ),
    (
        "完全性の主張",
        r"(?:完全に|完璧|全て解決|どんな要望も|何でも)",
        "例外があれば誤認になる。対応できる範囲を具体的に書く。",
    ),
    (
        "比較優位の主張",
        r"(?:他社より|他社と比べ|よりも安く|格安|激安)",
        "比較対象と根拠を示せなければ使えない。自社の事実だけを書く。",
    ),
    (
        "個人が特定される情報",
        # 「田中様邸」「田中邸」は拾い、匿名化済みの「S様邸」は拾わない。
        r"[一-鿿]{1,4}(?:様|さん)邸|[一-鿿]{2,4}邸",
        "施主が特定されうる。掲載許諾の条件を確認し、必要なら「S様邸」等に伏せる。",
    ),
    (
        "個人が特定される情報",
        r"\d{1,3}\s*丁目\s*\d{1,3}(?:\s*[-−]\s*\d{1,3})?|\d{1,3}[-−]\d{1,3}[-−]\d{1,4}\s*(?:番地)?",
        "番地レベルの所在地。市区町村までに丸めるべきか、掲載許諾の条件を確認する。",
    ),
    (
        "裏付けが要る数値",
        r"\d[\d,]*\s*(?:件|棟|組|名)(?:の実績|の施工)?(?:以上)?",
        "実績の件数は、案件台帳や社内記録で裏が取れたものだけ使う。取れないなら削除する。",
    ),
    (
        "裏付けが要る数値",
        r"(?:創業|設立|開業|業歴|実績)\s*(?:から)?\s*\d[\d,]*\s*年",
        "年数は社内記録で裏が取れたものだけ使う。取れないなら削除する。",
    ),
    (
        "裏付けが要る数値",
        r"\d[\d,]*(?:\.\d+)?\s*[%％]",
        "割合は算出根拠と母数を示せなければ使えない。",
    ),
)

_COMPILED = tuple((category, re.compile(pattern), reason) for category, pattern, reason in _RULES)

DISCLAIMER = (
    "既知のパターンによる機械的なチェックであり、適法性の判断ではない。"
    "見落としも誤検出もあるため、掲載可否は必ず人が判断すること。"
)


@dataclass(frozen=True)
class Flag:
    """指摘 1 件。"""

    category: str
    phrase: str
    line: int
    context: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "phrase": self.phrase,
            "line": self.line,
            "context": self.context,
            "reason": self.reason,
        }


def _context(text: str, start: int, end: int, width: int = 20) -> str:
    head = text[max(0, start - width) : start].replace("\n", " ")
    tail = text[end : end + width].replace("\n", " ")
    return f"{head}〈{text[start:end]}〉{tail}".strip()


def review_copy(text: str) -> dict[str, Any]:
    """原稿を走査し、確認が要る表現を返す。

    同じ位置に複数のルールが当たった場合は、最初に当たったものだけを残す。
    """
    if not text.strip():
        raise ValueError("チェックする原稿が空です")

    seen: set[tuple[int, int]] = set()
    flags: list[Flag] = []
    for category, pattern, reason in _COMPILED:
        for match in pattern.finditer(text):
            span = match.span()
            if any(span[0] < e and s < span[1] for s, e in seen):
                continue
            seen.add(span)
            flags.append(
                Flag(
                    category=category,
                    phrase=match.group(),
                    line=text.count("\n", 0, span[0]) + 1,
                    context=_context(text, *span),
                    reason=reason,
                )
            )

    flags.sort(key=lambda f: (f.line, f.phrase))
    by_category: dict[str, int] = {}
    for flag in flags:
        by_category[flag.category] = by_category.get(flag.category, 0) + 1

    return {
        "count": len(flags),
        "by_category": by_category,
        "flags": [flag.to_dict() for flag in flags],
        "disclaimer": DISCLAIMER,
    }
