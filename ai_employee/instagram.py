"""Instagram 投稿——構成の型と、投稿画像のデザイン生成。

このモジュールは画像そのものを作らない。1080×1080 のスライドを HTML として
組み立てるので、ブラウザで開いてスクリーンショットするか、PDF 出力して画像化する。

構成の型(POST_FORMATS)は、住宅・建築のアカウントでよく使われる投稿パターン。
「何を撮る必要があるか」まで含めて示すことで、素材がないまま原稿だけ作る事故を防ぐ。
"""

from __future__ import annotations

import html
from typing import Any

# 投稿の型。用途・1枚目のフック・構成・必要な素材をセットで持つ。
POST_FORMATS: dict[str, dict[str, Any]] = {
    "works": {
        "label": "施工事例カルーセル",
        "purpose": "設計の考え方を実例で伝え、問い合わせにつなげる",
        "hook": "1枚目は写真1枚と短い一文だけ。説明を詰め込まない。"
        "「どんな暮らしが実現したか」を言う(「○○様邸完成」は保存されない)。",
        "slides": [
            "表紙: 一番強い写真 + 一言のコンセプト",
            "課題: 敷地や暮らしの制約(狭い/北向き/三世代 など)",
            "解き方1: その制約にどう答えたか(写真 + 短い説明)",
            "解き方2: もう一つの工夫",
            "ディテール: 素材や納まりの寄り",
            "まとめ + 次の行動(相談会・資料請求への導線)",
        ],
        "assets": ["外観写真", "内観写真 3〜4 枚", "ディテールの寄り 1 枚", "平面図(任意)"],
    },
    "before_after": {
        "label": "ビフォーアフター",
        "purpose": "リノベ・改修の価値を一目で伝える",
        "hook": "1枚目に After を置き、2枚目で Before を見せる。順序を逆にしない。",
        "slides": [
            "表紙: After の写真 + 変化を一言で",
            "Before: 同じアングルの写真",
            "何を変えたか: 間取り・断熱・動線のどれを触ったか",
            "住まい手の変化: 暮らしがどう変わったか",
            "費用と期間の目安(出せる範囲で。出せないなら書かない)",
            "まとめ + 相談への導線",
        ],
        "assets": ["Before / After を同アングルで撮った写真", "工事中の写真(任意)"],
    },
    "knowledge": {
        "label": "住まいの豆知識",
        "purpose": "検討初期の層に届き、保存・シェアされる",
        "hook": "1枚目は「知らないと損する」ではなく、具体的な問いにする。"
        "例: 「北向きの土地、本当に暗い?」",
        "slides": [
            "表紙: 具体的な問い",
            "結論: 先に答えを出す",
            "理由1: なぜそう言えるか",
            "理由2: 例外・注意点",
            "実例: 自社の事例で補足(掲載許諾があるものだけ)",
            "まとめ + 保存を促す一言",
        ],
        "assets": ["図解 or 事例写真 2〜3 枚"],
    },
    "voice": {
        "label": "お客様の声",
        "purpose": "第三者の言葉で信頼を積む",
        "hook": "1枚目に施主の言葉をそのまま置く。会社の説明を先に置かない。",
        "slides": [
            "表紙: 施主の言葉の引用(短く)",
            "背景: どんな要望から始まったか",
            "検討中の不安: 何が心配だったか",
            "どう解決したか",
            "住んでからの感想",
            "まとめ + 相談への導線",
        ],
        "assets": ["竣工写真", "施主の言葉(掲載許諾が必須)"],
    },
    "event": {
        "label": "見学会・相談会の告知",
        "purpose": "日程が決まっている集客イベントに人を集める",
        "hook": "1枚目に「いつ・どこで・何が見られるか」を全部載せる。"
        "スクロールさせない。",
        "slides": [
            "表紙: 日時・場所・何が見られるか",
            "見どころ1",
            "見どころ2",
            "こんな方におすすめ",
            "予約方法と締切",
        ],
        "assets": ["会場の写真", "地図(任意)"],
    },
    "staff": {
        "label": "スタッフ・設計の裏側",
        "purpose": "人柄を見せて、相談のハードルを下げる",
        "hook": "1枚目は作業風景か手元の写真。ポートレートより仕事の様子。",
        "slides": [
            "表紙: 作業中の写真 + 何をしているか",
            "なぜこの検討をしているか",
            "こだわっている点",
            "相談時に聞かれることへの答え",
            "まとめ + 気軽な相談の呼びかけ",
        ],
        "assets": ["作業風景", "手描きスケッチや模型の写真"],
    },
}

# 配色。建築事務所のアカウントで使いやすい落ち着いた 3 種類。
THEMES: dict[str, dict[str, str]] = {
    "wood": {
        "label": "木質・ナチュラル",
        "bg": "#F4EFE7",
        "surface": "#FFFFFF",
        "ink": "#2E2A25",
        "muted": "#7A7168",
        "accent": "#9C6B3F",
        "on_accent": "#FBF7F2",
    },
    "mono": {
        "label": "モノトーン",
        "bg": "#F2F2F0",
        "surface": "#FFFFFF",
        "ink": "#1B1B1B",
        "muted": "#767676",
        "accent": "#1B1B1B",
        "on_accent": "#FFFFFF",
    },
    "night": {
        "label": "ダーク・重厚",
        "bg": "#1E1F21",
        "surface": "#26282B",
        "ink": "#F2F1EF",
        "muted": "#A5A29D",
        "accent": "#C89B5A",
        "on_accent": "#1E1F21",
    },
}

# スライドの種類。
SLIDE_KINDS = ("cover", "body", "cta")


class InstagramError(RuntimeError):
    """投稿デザインの組み立てに失敗した。"""


def post_format(name: str) -> dict[str, Any]:
    """投稿の型を返す。"""
    if name not in POST_FORMATS:
        raise InstagramError(
            f"不正な投稿の型です: {name} (選択肢: {'/'.join(POST_FORMATS)})"
        )
    return {"key": name, **POST_FORMATS[name]}


def _esc(value: str) -> str:
    return html.escape(value or "", quote=False).replace("\n", "<br>")


def _slide_html(
    index: int, total: int, slide: dict[str, Any], brand: str = ""
) -> str:
    kind = slide.get("kind", "body")
    title = _esc(slide.get("title", ""))
    body = _esc(slide.get("body", ""))
    label = _esc(slide.get("label", ""))

    if kind == "cover":
        inner = f"""
      <div class="label">{label}</div>
      <h1>{title}</h1>
      <p class="lead">{body}</p>"""
    elif kind == "cta":
        inner = f"""
      <div class="label">{label}</div>
      <h2>{title}</h2>
      <p class="cta">{body}</p>"""
    else:
        inner = f"""
      <div class="label">{label}</div>
      <h2>{title}</h2>
      <p>{body}</p>"""

    brand_html = f'\n    <div class="brand">{_esc(brand)}</div>' if brand.strip() else ""
    return f"""  <section class="slide {kind}">
    <div class="frame">{inner}
    </div>{brand_html}
    <div class="page">{index} / {total}</div>
  </section>"""


def build_design(
    slides: list[dict[str, Any]],
    theme: str = "wood",
    brand: str = "",
    footer: str = "",
) -> str:
    """1080×1080 のスライドを HTML として組み立てる。

    画像は作らない。ブラウザで開いてスクリーンショットするか、
    印刷ダイアログから PDF に出して画像化する。
    """
    if not slides:
        raise InstagramError("スライドが 1 枚もありません")
    if theme not in THEMES:
        raise InstagramError(f"不正なテーマです: {theme} (選択肢: {'/'.join(THEMES)})")
    for slide in slides:
        kind = slide.get("kind", "body")
        if kind not in SLIDE_KINDS:
            raise InstagramError(
                f"不正なスライド種別です: {kind} (選択肢: {'/'.join(SLIDE_KINDS)})"
            )
        if not str(slide.get("title", "")).strip():
            raise InstagramError("各スライドには title が必要です")

    palette = THEMES[theme]
    total = len(slides)
    body = "\n".join(
        _slide_html(i, total, slide, brand) for i, slide in enumerate(slides, 1)
    )
    footer_html = (
        f'\n  <p class="footer">{_esc(footer)}</p>' if footer.strip() else ""
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Instagram 投稿デザイン</title>
<style>
  :root {{
    --bg: {palette["bg"]};
    --surface: {palette["surface"]};
    --ink: {palette["ink"]};
    --muted: {palette["muted"]};
    --accent: {palette["accent"]};
    --on-accent: {palette["on_accent"]};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 40px;
    background: #8a8a8a;
    font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic",
                 "Noto Sans JP", sans-serif;
    display: flex;
    flex-wrap: wrap;
    gap: 32px;
    justify-content: center;
  }}
  .slide {{
    width: 1080px;
    height: 1080px;
    position: relative;
    display: flex;
    align-items: center;
    padding: 104px;
    flex: 0 0 auto;
    background: var(--surface);
    color: var(--ink);
  }}
  /* 表紙は地色を敷いて、フィードの中で白い箱に見えないようにする */
  .slide.cover {{ background: var(--bg); }}
  /* 締めのスライドは反転させて、行動の呼びかけを目立たせる */
  .slide.cta {{ background: var(--accent); color: var(--on-accent); }}

  .frame {{ width: 100%; }}
  .cover .frame,
  .body  .frame {{ border-left: 8px solid var(--accent); padding-left: 56px; }}

  .label {{
    font-size: 30px;
    letter-spacing: 0.24em;
    color: var(--accent);
    margin-bottom: 40px;
    font-weight: 700;
  }}
  .cta .label {{ color: var(--on-accent); opacity: 0.75; }}

  h1 {{ font-size: 96px; line-height: 1.28; margin: 0 0 44px; font-weight: 700; }}
  h2 {{ font-size: 68px; line-height: 1.35; margin: 0 0 40px; font-weight: 700; }}
  p  {{ font-size: 40px; line-height: 1.85; margin: 0; color: var(--muted); }}
  .lead {{ font-size: 46px; color: var(--ink); }}
  .cta h2 {{ font-size: 76px; }}
  .cta p  {{ font-size: 44px; color: var(--on-accent); opacity: 0.9; }}

  .brand {{
    position: absolute;
    left: 104px;
    bottom: 88px;
    font-size: 28px;
    letter-spacing: 0.14em;
    color: var(--muted);
  }}
  .cta .brand {{ color: var(--on-accent); opacity: 0.75; }}
  .page {{
    position: absolute;
    right: 104px;
    bottom: 88px;
    font-size: 28px;
    letter-spacing: 0.12em;
    color: var(--muted);
  }}
  .cta .page {{ color: var(--on-accent); opacity: 0.6; }}
  .footer {{
    flex: 1 0 100%;
    text-align: center;
    color: #fff;
    font-size: 16px;
    margin: 8px 0 0;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; gap: 0; }}
    .slide {{ page-break-after: always; }}
    .footer {{ display: none; }}
  }}
</style>
</head>
<body>
{body}{footer_html}
</body>
</html>
"""
