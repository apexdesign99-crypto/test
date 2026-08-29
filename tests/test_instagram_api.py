"""Instagram Graph API 連携のテスト。

実際の API は叩かない。通信部分を差し替えて、
資格情報の扱いと欠測の扱いを重点的に確認する。
"""

import json
from datetime import timedelta

import pytest

from ai_employee.instagram_api import (
    MEDIA_METRICS,
    RETIRED_METRICS,
    Credentials,
    InstagramAPIError,
    InstagramClient,
    connect,
    credentials_path,
    load_credentials,
    load_metrics,
    save_credentials,
    sync,
)
from ai_employee.workspace import now

ACCOUNT = {
    "id": "178", "username": "apex_sekkei", "account_type": "BUSINESS",
    "media_count": 42, "followers_count": 1280,
}
MEDIA = [
    {"id": "m1", "caption": "北向きの敷地に", "media_type": "CAROUSEL_ALBUM",
     "permalink": "https://instagram.com/p/x1", "timestamp": "2026-09-03T10:00:00+0000",
     "like_count": 84, "comments_count": 6},
    {"id": "m2", "caption": "無垢の床", "media_type": "IMAGE",
     "permalink": "https://instagram.com/p/x2", "timestamp": "2026-09-08T10:00:00+0000",
     "like_count": 131, "comments_count": 11},
]


def make_transport(fail_insights: set[str] | None = None, calls: list | None = None):
    """偽の API。呼ばれた URL を記録する。"""
    fail = fail_insights or set()

    def transport(url: str) -> dict:
        if calls is not None:
            calls.append(url)
        if "/me?" in url:
            return ACCOUNT
        if "me/media" in url:
            return {"data": MEDIA}
        if "insights" in url:
            media_id = url.split("/")[3].split("/")[0]
            if media_id in fail:
                raise InstagramAPIError("Instagram API エラー (100): metric not available")
            return {"data": [
                {"name": "views", "values": [{"value": 3120}]},
                {"name": "reach", "values": [{"value": 2410}]},
                {"name": "saved", "values": [{"value": 58}]},
            ]}
        if "refresh_access_token" in url:
            return {"access_token": "NEW_TOKEN", "expires_in": 60 * 60 * 24 * 60}
        return {}

    return transport


# ---------------------------------------------------------------- 指標の定義


def test_廃止済みの指標を要求しない():
    """impressions などは 2025 年に廃止され、要求するとエラーになる。"""
    assert not set(MEDIA_METRICS) & set(RETIRED_METRICS)
    assert "views" in MEDIA_METRICS      # impressions の後継
    assert "impressions" in RETIRED_METRICS


def test_取得する指標に廃止済みが混ざらない(tmp_path):
    calls: list[str] = []
    connect("TOKEN", tmp_path, make_transport(calls=calls))
    sync(tmp_path, 25, make_transport(calls=calls))
    requested = " ".join(calls)
    for retired in RETIRED_METRICS:
        assert retired not in requested, retired


# ------------------------------------------------------------ 資格情報


def test_接続前にトークンを検証する(tmp_path):
    """使えないトークンを保存しても意味がない。"""
    def rejecting(url: str) -> dict:
        raise InstagramAPIError("Instagram API エラー (190): invalid token")

    with pytest.raises(InstagramAPIError, match="190"):
        connect("BAD", tmp_path, rejecting)
    assert not credentials_path(tmp_path).is_file()


def test_接続するとアカウント情報が付く(tmp_path):
    credentials = connect("TOKEN", tmp_path, make_transport())
    assert credentials.username == "apex_sekkei"
    assert credentials.user_id == "178"
    assert credentials.days_left() == 59        # 60日 - 端数


def test_空のトークンは拒否される(tmp_path):
    with pytest.raises(InstagramAPIError, match="空です"):
        connect("   ", tmp_path)


def test_保存先は所有者だけが読める(tmp_path):
    connect("TOKEN", tmp_path, make_transport())
    mode = credentials_path(tmp_path).stat().st_mode & 0o777
    assert mode == 0o600


def test_画面に出す情報にトークンを含めない():
    """ここが要。ログにも画面にも漏らさない。"""
    credentials = Credentials(access_token="SECRET_TOKEN", username="apex")
    assert "SECRET_TOKEN" not in json.dumps(credentials.summary(), ensure_ascii=False)


def test_環境変数からも読める(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "FROM_ENV")
    credentials = load_credentials(tmp_path)
    assert credentials is not None and credentials.access_token == "FROM_ENV"


def test_未接続ならNoneを返す(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    assert load_credentials(tmp_path) is None


@pytest.mark.parametrize(
    "days,needs_refresh,expired",
    [(59, False, False), (14, True, False), (1, True, False), (-3, True, True)],
)
def test_期限の判定(days, needs_refresh, expired):
    credentials = Credentials(
        access_token="T",
        expires_at=(now() + timedelta(days=days, hours=1)).isoformat(timespec="seconds"),
    )
    assert credentials.needs_refresh() is needs_refresh
    assert credentials.is_expired() is expired


def test_期限が不明でも落ちない():
    credentials = Credentials(access_token="T")
    assert credentials.days_left() is None
    assert credentials.needs_refresh() is False
    assert credentials.is_expired() is False


# ---------------------------------------------------------------- 取得


def test_アカウント情報を取れる(tmp_path):
    client = InstagramClient(Credentials(access_token="T"), make_transport())
    assert client.me()["username"] == "apex_sekkei"


def test_トークンなしのクライアントは作れない():
    with pytest.raises(InstagramAPIError, match="アクセストークンが設定されていません"):
        InstagramClient(Credentials(access_token=""))


@pytest.mark.parametrize("limit", [0, 101, -1])
def test_件数の範囲外は拒否される(limit):
    client = InstagramClient(Credentials(access_token="T"), make_transport())
    with pytest.raises(InstagramAPIError, match="1〜100"):
        client.recent_media(limit)


def test_指標を平坦な辞書で返す(tmp_path):
    client = InstagramClient(Credentials(access_token="T"), make_transport())
    assert client.media_insights("m1") == {"views": 3120, "reach": 2410, "saved": 58}


def test_トークンを更新できる(tmp_path):
    client = InstagramClient(Credentials(access_token="OLD"), make_transport())
    updated = client.refresh_token()
    assert updated.access_token == "NEW_TOKEN"
    assert updated.days_left() == 59


def test_更新後のトークンが返らなければエラー():
    client = InstagramClient(Credentials(access_token="T"), lambda url: {})
    with pytest.raises(InstagramAPIError, match="受け取れませんでした"):
        client.refresh_token()


# ---------------------------------------------------------------- 取り込み


def test_取り込んで保存できる(tmp_path):
    connect("TOKEN", tmp_path, make_transport())
    result = sync(tmp_path, 25, make_transport())
    assert result["account"]["followers_count"] == 1280
    assert len(result["media"]) == 2
    assert result["media"][0]["insights"]["saved"] == 58
    assert load_metrics(tmp_path)["synced_at"]


def test_指標が取れない投稿は欠測として残す(tmp_path):
    """0 と「取得できなかった」を混ぜない。"""
    connect("TOKEN", tmp_path, make_transport())
    result = sync(tmp_path, 25, make_transport(fail_insights={"m2"}))

    ok, failed = result["media"]
    assert ok["insights"]["views"] == 3120
    assert failed["insights"] == {}                 # 0 を入れない
    assert "metric not available" in failed["insights_error"]
    assert "0 ではない" in result["note"]


def test_1件の失敗で全体が止まらない(tmp_path):
    connect("TOKEN", tmp_path, make_transport())
    result = sync(tmp_path, 25, make_transport(fail_insights={"m1", "m2"}))
    assert len(result["media"]) == 2                # 件数は落ちない


def test_保存した指標にトークンが混ざらない(tmp_path):
    from ai_employee.instagram_api import metrics_path

    connect("SECRET_TOKEN", tmp_path, make_transport())
    sync(tmp_path, 25, make_transport())
    assert "SECRET_TOKEN" not in metrics_path(tmp_path).read_text(encoding="utf-8")


def test_未接続なら手順を案内して失敗する(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    with pytest.raises(InstagramAPIError, match="instagram-setup.md"):
        sync(tmp_path)


def test_期限切れなら取り直しを促す(tmp_path):
    save_credentials(Credentials(
        access_token="OLD",
        expires_at=(now() - timedelta(days=3)).isoformat(timespec="seconds"),
    ), tmp_path)
    with pytest.raises(InstagramAPIError, match="取り直しが必要"):
        sync(tmp_path, 25, make_transport())


def test_取り込み前は空の実績を返す(tmp_path):
    metrics = load_metrics(tmp_path)
    assert metrics["synced_at"] is None and metrics["media"] == []
