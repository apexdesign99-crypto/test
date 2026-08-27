"""Instagram 投稿デザインのテスト。

画像は生成できないので、HTML が正しく組み上がることと、
入力の取り違えを確実に弾くことを確認する。
"""

import re

import pytest

from ai_employee.instagram import (
    POST_FORMATS,
    THEMES,
    InstagramError,
    build_design,
    post_format,
)

SLIDES = [
    {"kind": "cover", "label": "施工事例", "title": "北向きの敷地に、光が回る家", "body": "愛知県一宮市"},
    {"kind": "body", "label": "課題", "title": "南に隣家が迫っていた", "body": "採光が期待できない敷地。"},
    {"kind": "cta", "label": "ご相談", "title": "土地探しからご相談ください", "body": "プロフィールのリンクから。"},
]


# ---------------------------------------------------------------- 投稿の型


def test_全ての型に必要な素材まで定義されている():
    for key, data in POST_FORMATS.items():
        assert data["label"], key
        assert data["purpose"], key
        assert data["hook"], key
        assert len(data["slides"]) >= 4, key
        assert data["assets"], key  # 素材が揃うか判断できるように


def test_型を取得できる():
    data = post_format("works")
    assert data["key"] == "works"
    assert data["label"] == "施工事例カルーセル"


def test_不正な型は選択肢つきで拒否される():
    with pytest.raises(InstagramError, match="不正な投稿の型"):
        post_format("バズる投稿")


# ---------------------------------------------------------------- デザイン


def test_スライド数ぶんのセクションが出る():
    html = build_design(SLIDES)
    assert html.count('<section class="slide') == 3
    assert "1 / 3" in html and "3 / 3" in html


def test_1080四方で組まれる():
    html = build_design(SLIDES)
    assert "width: 1080px" in html
    assert "height: 1080px" in html


def test_テーマの色が反映される():
    wood = build_design(SLIDES, theme="wood")
    night = build_design(SLIDES, theme="night")
    assert THEMES["wood"]["accent"] in wood
    assert THEMES["night"]["accent"] in night
    assert THEMES["night"]["accent"] not in wood


def test_事務所名が各スライドに入る():
    html = build_design(SLIDES, brand="アペックス設計事務所")
    assert html.count("アペックス設計事務所") == 3


def test_事務所名がなければブランド行を出さない():
    assert 'class="brand"' not in build_design(SLIDES)


def test_改行は_br_になる():
    html = build_design([{"kind": "cover", "title": "一行目\n二行目"}])
    assert "一行目<br>二行目" in html


def test_HTMLは常にエスケープされる():
    """原稿にタグが混ざってもレイアウトを壊さない。"""
    html = build_design([{"kind": "cover", "title": "<script>alert(1)</script>"}])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_印刷用のページ区切りが入る():
    """PDF 出力で 1 枚ずつに分かれるように。"""
    assert "page-break-after" in build_design(SLIDES)


# ---------------------------------------------------------------- 入力検証


def test_スライドが空なら拒否される():
    with pytest.raises(InstagramError, match="1 枚もありません"):
        build_design([])


def test_見出しのないスライドは拒否される():
    with pytest.raises(InstagramError, match="title が必要"):
        build_design([{"kind": "cover", "body": "本文だけ"}])


def test_不正なテーマとスライド種別は拒否される():
    with pytest.raises(InstagramError, match="不正なテーマ"):
        build_design(SLIDES, theme="ネオン")
    with pytest.raises(InstagramError, match="不正なスライド種別"):
        build_design([{"kind": "動画", "title": "x"}])


def test_種別を省略すると本文扱いになる():
    html = build_design([{"title": "見出しだけ"}])
    assert 'class="slide body"' in html


def test_生成されるHTMLは単体で完結する():
    """外部の CSS やフォントを読まないので、オフラインでも同じ見た目になる。"""
    html = build_design(SLIDES)
    assert html.startswith("<!doctype html>")
    assert not re.search(r'<link[^>]+href=', html)
    assert "<script" not in html
