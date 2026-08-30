"""投稿公開のテスト。

ここは唯一、外部に不可逆な変更を加える機能。
「止めるべきときに止まる」「下見では何も送らない」を重点的に確認する。
"""

import pytest

from ai_employee.instagram_api import Credentials
from ai_employee.instagram_publish import (
    CAPTION_MAX,
    PUBLISH_SCOPE,
    PublishError,
    Publisher,
)


def make_publisher(statuses=None, calls=None):
    """偽の通信層。送られた内容を記録する。"""
    sent = calls if calls is not None else []
    queue = iter(statuses or ["FINISHED"])

    def poster(url, data):
        sent.append(("POST", url, data))
        if url.endswith("/me/media"):
            return {"id": "container_1"}
        if url.endswith("/media_publish"):
            return {"id": "media_999"}
        return {}

    def getter(url):
        sent.append(("GET", url.split("?")[0], {}))
        try:
            return {"status_code": next(queue)}
        except StopIteration:
            return {"status_code": "FINISHED"}

    publisher = Publisher(
        Credentials(access_token="T"), poster, getter, sleeper=lambda s: None
    )
    return publisher, sent


# ---------------------------------------------------------------- 下見


def test_確認なしでは何も送らない():
    """ここが要。うっかり実行しても投稿されない。"""
    publisher, sent = make_publisher()
    result = publisher.publish("https://example.com/a.jpg", "本文")
    assert result["dry_run"] is True
    assert result["published"] is False
    assert sent == []                       # API を一度も叩いていない


def test_下見は投稿内容をそのまま返す():
    publisher, _ = make_publisher()
    result = publisher.publish("https://example.com/a.jpg", "無垢の床")
    assert result["image_url"] == "https://example.com/a.jpg"
    assert result["caption"] == "無垢の床"
    assert result["caption_length"] == 4
    assert "取り消せない" in result["note"]


# ------------------------------------------------------------ 止める条件


def test_手元のファイルは公開できない():
    """Instagram のサーバが取りに行くため、ローカルパスは使えない。"""
    publisher, _ = make_publisher()
    result = publisher.preflight("/Users/apex/post.png", "本文")
    assert result["can_publish"] is False
    assert "手元の PC のファイルは投稿できません" in result["blockers"][0]


@pytest.mark.parametrize(
    "url", ["http://example.com/a.jpg", "ftp://x/a.jpg", "example.com/a.jpg", ""]
)
def test_HTTPS以外は拒否される(url):
    publisher, _ = make_publisher()
    assert publisher.preflight(url, "本文")["can_publish"] is False


def test_許諾のない案件は公開できない():
    publisher, _ = make_publisher()
    result = publisher.preflight(
        "https://example.com/a.jpg", "本文",
        consent={"publishable": False, "consent_status": "未確認",
                 "guidance": "許諾を得る必要がある。"},
    )
    assert result["can_publish"] is False
    assert "掲載許諾が「未確認」" in result["blockers"][0]


def test_投稿済みは二重に公開できない():
    publisher, _ = make_publisher()
    result = publisher.preflight(
        "https://example.com/a.jpg", "本文",
        plan_post={"id": "abc", "status": "投稿済", "assets_ready": True},
    )
    assert result["can_publish"] is False
    assert "二重投稿を防ぐため" in result["blockers"][0]


def test_長すぎるキャプションは拒否される():
    publisher, _ = make_publisher()
    result = publisher.preflight("https://example.com/a.jpg", "あ" * (CAPTION_MAX + 1))
    assert result["can_publish"] is False


def test_止める条件は確認フラグでも上書きできない():
    """ここが要。--confirm を付けても通さない。"""
    publisher, sent = make_publisher()
    with pytest.raises(PublishError, match="公開を中止しました"):
        publisher.publish(
            "https://example.com/a.jpg", "本文", confirm=True,
            consent={"publishable": False, "consent_status": "不可"},
        )
    assert sent == []                       # 中止したので何も送っていない


# ---------------------------------------------------------------- 警告


def test_掲載条件は警告として出る():
    publisher, _ = make_publisher()
    result = publisher.preflight(
        "https://example.com/a.jpg", "本文",
        consent={"publishable": True, "consent_status": "条件付き",
                 "conditions": "施主名は伏せる"},
    )
    assert result["can_publish"] is True     # 止めはしない
    assert any("施主名は伏せる" in w for w in result["warnings"])


def test_表現の指摘は警告として出る():
    publisher, _ = make_publisher()
    result = publisher.preflight(
        "https://example.com/a.jpg", "地域No.1の設計事務所。必ずご満足いただけます。")
    assert result["can_publish"] is True     # 判断は人がする
    assert result["copy_findings"] == 2
    assert any("No.1" in w for w in result["warnings"])


def test_素材未確認は警告として出る():
    publisher, _ = make_publisher()
    result = publisher.preflight(
        "https://example.com/a.jpg", "本文",
        plan_post={"id": "abc", "status": "原稿済", "assets_ready": False})
    assert result["can_publish"] is True
    assert any("素材が未確認" in w for w in result["warnings"])


def test_空のキャプションは警告になる():
    publisher, _ = make_publisher()
    result = publisher.preflight("https://example.com/a.jpg", "")
    assert result["can_publish"] is True
    assert any("キャプションが空" in w for w in result["warnings"])


# ---------------------------------------------------------------- 公開


def test_二段階で公開する():
    """コンテナを作り、処理完了を待ってから公開する。"""
    publisher, sent = make_publisher(statuses=["IN_PROGRESS", "FINISHED"])
    result = publisher.publish("https://example.com/a.jpg", "本文", confirm=True)

    assert result["published"] is True
    assert result["media_id"] == "media_999"
    kinds = [(k, u.rsplit("/", 1)[-1]) for k, u, _ in sent]
    assert kinds[0] == ("POST", "media")
    assert kinds[-1] == ("POST", "media_publish")
    assert ("GET", "container_1") in kinds     # 完了を待っている


def test_公開時にキャプションと画像を送る():
    publisher, sent = make_publisher()
    publisher.publish("https://example.com/a.jpg", "無垢の床", confirm=True)
    _, _, data = sent[0]
    assert data["image_url"] == "https://example.com/a.jpg"
    assert data["caption"] == "無垢の床"


def test_画像を取得できなければ理由を示して止まる():
    publisher, _ = make_publisher(statuses=["ERROR"])
    with pytest.raises(PublishError, match="画像を取得できませんでした"):
        publisher.publish("https://example.com/a.jpg", "本文", confirm=True)


def test_処理が終わらなければ打ち切る():
    publisher, _ = make_publisher(statuses=["IN_PROGRESS"] * 100)
    with pytest.raises(PublishError, match="秒待っても"):
        publisher.publish("https://example.com/a.jpg", "本文", confirm=True)


def test_コンテナIDが返らなければ止まる():
    publisher = Publisher(Credentials(access_token="T"), lambda u, d: {},
                          lambda u: {"status_code": "FINISHED"}, lambda s: None)
    with pytest.raises(PublishError, match="コンテナの作成に失敗"):
        publisher.publish("https://example.com/a.jpg", "本文", confirm=True)


def test_既定でも画像の取得完了を待つ():
    """テストが偽の getter を渡していたため、本番だけ待たずに公開する不具合があった。

    完了前に publish を投げると失敗する。既定でも実際に問い合わせること。
    """
    from ai_employee.instagram_api import http_get

    publisher = Publisher(Credentials(access_token="T"))
    assert publisher._getter is http_get


def test_トークンなしでは作れない():
    with pytest.raises(PublishError, match="アクセストークンが設定されていません"):
        Publisher(Credentials(access_token=""))


# ------------------------------------------------------------ 権限


def test_公開には追加の権限が必要である旨を持つ():
    assert PUBLISH_SCOPE == "instagram_business_content_publish"


def test_AI社員には公開ツールを渡していない():
    """誤投稿は取り消せないため、実行は人に限る。"""
    import tempfile
    from pathlib import Path

    from ai_employee.tools import build_tools
    from ai_employee.workspace import Workspace

    workspace = Workspace("marke", Path(tempfile.mkdtemp()))
    workspace.ensure()
    names = set(build_tools(workspace))
    for forbidden in ("publish", "publish_post", "media_publish"):
        assert forbidden not in names
    assert not any("publish" in name and name != "log_publication"
                   and name != "publication_status" and name != "publication_candidates"
                   for name in names)
