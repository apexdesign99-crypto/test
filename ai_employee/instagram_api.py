"""Instagram Graph API との連携(読み取りのみ)。

「Instagram API with Instagram Login」の経路を使う。Facebook ページの連携が
不要で、自社アカウントだけを見るなら App Review も要らないため。

Python 標準ライブラリだけで動く。追加インストールは要らない。

**読み取り専用。** 投稿の自動公開は行わない。別の権限が必要なうえ、
誤投稿の影響が大きいため、公開は人が Instagram アプリから行う前提。

**アクセストークンは資格情報。** 画面にもログにも出さない。
保存先は所有者だけが読めるパーミッションにする。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from .config import office_root
from .workspace import now

BASE_URL = "https://graph.instagram.com"

# 環境変数でトークンを渡す場合の名前。
ENV_TOKEN = "INSTAGRAM_ACCESS_TOKEN"

# 取得する投稿の項目。
MEDIA_FIELDS = (
    "id", "caption", "media_type", "media_product_type",
    "permalink", "timestamp", "like_count", "comments_count",
)

# 投稿ごとの指標。**2025 年の変更で impressions と video_views は廃止された。**
# views がすべての形式(フィード・カルーセル・リール)の共通指標になっている。
MEDIA_METRICS = ("views", "reach", "saved", "shares", "total_interactions")

# 廃止済みの指標。要求するとエラーになるので、社員にも渡さない。
RETIRED_METRICS = (
    "impressions", "video_views", "profile_views", "website_clicks",
    "email_contacts", "phone_call_clicks", "text_message_clicks",
)

# トークンの残りがこの日数を切ったら警告する。
REFRESH_WARNING_DAYS = 14


class InstagramAPIError(RuntimeError):
    """API 呼び出しに失敗した。"""


@dataclass
class Credentials:
    """長期アクセストークンと、その有効期限。"""

    access_token: str
    user_id: str = ""
    username: str = ""
    obtained_at: str = ""
    expires_at: str = ""

    def days_left(self) -> int | None:
        if not self.expires_at:
            return None
        from datetime import datetime

        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return None
        return (expires - now()).days

    def needs_refresh(self) -> bool:
        left = self.days_left()
        return left is not None and left <= REFRESH_WARNING_DAYS

    def is_expired(self) -> bool:
        left = self.days_left()
        return left is not None and left < 0

    def summary(self) -> dict[str, Any]:
        """画面や社員に見せてよい情報だけ。**トークン本体は含めない。**"""
        return {
            "username": self.username,
            "user_id": self.user_id,
            "obtained_at": self.obtained_at,
            "expires_at": self.expires_at,
            "days_left": self.days_left(),
            "needs_refresh": self.needs_refresh(),
            "expired": self.is_expired(),
        }


def credentials_path(root: Path | None = None) -> Path:
    return (root or office_root()) / "_company" / "instagram_credentials.json"


def save_credentials(credentials: Credentials, root: Path | None = None) -> Path:
    """資格情報を保存する。所有者だけが読めるようにする。"""
    path = credentials_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(credentials), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows など
        pass
    return path


def load_credentials(root: Path | None = None) -> Credentials | None:
    """保存済み、または環境変数からトークンを読む。"""
    path = credentials_path(root)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = set(Credentials.__dataclass_fields__)
        return Credentials(**{k: v for k, v in data.items() if k in known})
    token = os.environ.get(ENV_TOKEN)
    if token:
        return Credentials(access_token=token)
    return None


def _default_transport(url: str) -> dict[str, Any]:
    """実際に HTTP を叩く。テストではここを差し替える。"""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(body).get("error", {})
            message = error.get("message", body)
            code = error.get("code", exc.code)
        except json.JSONDecodeError:
            message, code = body, exc.code
        raise InstagramAPIError(f"Instagram API エラー ({code}): {message}") from None
    except urllib.error.URLError as exc:
        raise InstagramAPIError(f"Instagram に接続できません: {exc.reason}") from None
    except json.JSONDecodeError:
        raise InstagramAPIError("Instagram の応答を解釈できませんでした") from None


class InstagramClient:
    """Instagram Graph API の読み取りクライアント。"""

    def __init__(
        self,
        credentials: Credentials,
        transport: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        if not credentials.access_token:
            raise InstagramAPIError("アクセストークンが設定されていません")
        self.credentials = credentials
        self._transport = transport or _default_transport

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        params["access_token"] = self.credentials.access_token
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        return self._transport(f"{BASE_URL}/{path.lstrip('/')}?{query}")

    # ------------------------------------------------------------ 取得

    def me(self) -> dict[str, Any]:
        """アカウント情報。接続確認にも使う。"""
        return self._get(
            "me", fields="id,username,account_type,media_count,followers_count"
        )

    def recent_media(self, limit: int = 25) -> list[dict[str, Any]]:
        """最近の投稿を新しい順に返す。"""
        if not 1 <= limit <= 100:
            raise InstagramAPIError("取得件数は 1〜100 で指定してください")
        result = self._get("me/media", fields=",".join(MEDIA_FIELDS), limit=limit)
        return result.get("data", [])

    def media_insights(self, media_id: str) -> dict[str, Any]:
        """投稿 1 件の指標。取得できなかった指標は含めずに返す。"""
        result = self._get(f"{media_id}/insights", metric=",".join(MEDIA_METRICS))
        values: dict[str, Any] = {}
        for entry in result.get("data", []):
            series = entry.get("values") or []
            if series:
                values[entry["name"]] = series[0].get("value")
        return values

    def refresh_token(self) -> Credentials:
        """長期トークンを更新する(さらに 60 日)。

        更新できるのは、発行から 24 時間以上経過した有効なトークンだけ。
        期限が切れていると更新できず、取り直しになる。
        """
        result = self._transport(
            f"{BASE_URL}/refresh_access_token?"
            + urllib.parse.urlencode({
                "grant_type": "ig_refresh_token",
                "access_token": self.credentials.access_token,
            })
        )
        token = result.get("access_token")
        if not token:
            raise InstagramAPIError("更新後のトークンを受け取れませんでした")
        seconds = int(result.get("expires_in", 0))
        stamp = now()
        return Credentials(
            access_token=token,
            user_id=self.credentials.user_id,
            username=self.credentials.username,
            obtained_at=stamp.isoformat(timespec="seconds"),
            expires_at=(stamp + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
        )


def connect(token: str, root: Path | None = None,
            transport: Callable[[str], dict[str, Any]] | None = None) -> Credentials:
    """トークンを検証し、アカウント情報を添えて保存する。

    保存前に必ず /me を叩く。使えないトークンを保存しても意味がないため。
    """
    if not token.strip():
        raise InstagramAPIError("アクセストークンが空です")
    client = InstagramClient(Credentials(access_token=token.strip()), transport)
    account = client.me()
    stamp = now()
    credentials = Credentials(
        access_token=token.strip(),
        user_id=str(account.get("id", "")),
        username=account.get("username", ""),
        obtained_at=stamp.isoformat(timespec="seconds"),
        # 長期トークンの有効期間は 60 日。実際の期限は更新時に上書きされる。
        expires_at=(stamp + timedelta(days=60)).isoformat(timespec="seconds"),
    )
    save_credentials(credentials, root)
    return credentials


# ------------------------------------------------------------------ 実績


def metrics_path(root: Path | None = None) -> Path:
    return (root or office_root()) / "_company" / "instagram_metrics.json"


def load_metrics(root: Path | None = None) -> dict[str, Any]:
    path = metrics_path(root)
    if not path.is_file():
        return {"synced_at": None, "account": {}, "media": []}
    return json.loads(path.read_text(encoding="utf-8"))


def sync(
    root: Path | None = None,
    limit: int = 25,
    transport: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """投稿と指標を取り込んで保存する。

    取れなかった指標は欠測として残す。0 として記録すると、
    「反応がなかった」と「取得できなかった」が区別できなくなるため。
    """
    credentials = load_credentials(root)
    if credentials is None:
        raise InstagramAPIError(
            "Instagram に接続していません。"
            "docs/instagram-setup.md の手順でトークンを取得し、"
            "`python -m ai_employee instagram --connect <token>` を実行してください。"
        )
    if credentials.is_expired():
        raise InstagramAPIError(
            "アクセストークンの有効期限が切れています。"
            "期限切れのトークンは更新できないため、取り直しが必要です。"
        )

    client = InstagramClient(credentials, transport)
    account = client.me()
    media = []
    for item in client.recent_media(limit):
        record = dict(item)
        try:
            record["insights"] = client.media_insights(item["id"])
            record["insights_error"] = None
        except InstagramAPIError as exc:
            # 1 件の失敗で全体を止めない。取れなかったことを残す。
            record["insights"] = {}
            record["insights_error"] = str(exc)
        media.append(record)

    payload = {
        "synced_at": now().isoformat(timespec="seconds"),
        "account": {
            "username": account.get("username"),
            "followers_count": account.get("followers_count"),
            "media_count": account.get("media_count"),
            "account_type": account.get("account_type"),
        },
        "media": media,
        "note": "insights が空の投稿は取得できなかったもの。0 ではない。",
    }
    path = metrics_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload
