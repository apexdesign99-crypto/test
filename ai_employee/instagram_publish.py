"""Instagram への投稿公開。

**ここはこのツールで唯一、外部に不可逆な変更を加える機能。**
誤投稿は取り消せない(削除しても見た人には届いている)ため、
次の安全策を設けている。

1. **AI社員には渡さない。** 公開ツールは社員のツール一覧に入れない。
   社員は原稿と計画を用意するところまで。実行は人が行う。
2. **既定は下見。** `confirm=True` を明示しない限り、何が投稿されるかを
   見せるだけで公開しない。
3. **止める条件は上書きできない。** 掲載許諾がない、既に投稿済み、
   という2つは確認フラグでも通さない。
4. **表現チェックの結果を下見に出す。** 判断は人が行う。

画像は Instagram のサーバが取りに行くため、**公開された HTTPS の URL**
でなければならない。手元の PC にあるファイルは直接投稿できない。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from .copycheck import review_copy
from .instagram_api import BASE_URL, Credentials, InstagramAPIError, http_get

# 公開に必要な追加の権限。読み取りだけなら要らない。
PUBLISH_SCOPE = "instagram_business_content_publish"

# 画像の要件(Meta の仕様)。
IMAGE_REQUIREMENTS = (
    "JPEG 形式",
    "縦横比 4:5 〜 1.91:1",
    "幅 320〜1440px",
    "8MB 以下",
    "公開された HTTPS の直接 URL(HTML ページやリダイレクトは不可)",
)

# コンテナの処理待ちの上限。
CONTAINER_TIMEOUT_SECONDS = 120
CONTAINER_POLL_SECONDS = 3

# キャプションの上限(Meta の仕様)。
CAPTION_MAX = 2200


class PublishError(RuntimeError):
    """公開を止めた、または失敗した。"""


def _post(url: str, data: dict[str, Any]) -> dict[str, Any]:
    """POST を投げる。テストではここを差し替える。"""
    payload = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            error = json.loads(body).get("error", {})
            raise InstagramAPIError(
                f"Instagram API エラー ({error.get('code', exc.code)}): "
                f"{error.get('message', body)}"
            ) from None
        except json.JSONDecodeError:
            raise InstagramAPIError(f"Instagram API エラー ({exc.code}): {body}") from None
    except urllib.error.URLError as exc:
        raise InstagramAPIError(f"Instagram に接続できません: {exc.reason}") from None


class Publisher:
    """投稿の公開。下見と実行を分けて扱う。"""

    def __init__(
        self,
        credentials: Credentials,
        poster: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        getter: Callable[[str], dict[str, Any]] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not credentials.access_token:
            raise PublishError("アクセストークンが設定されていません")
        self.credentials = credentials
        self._post = poster or _post
        # 既定でも実際に問い合わせる。ここを None のままにすると、
        # Instagram が画像を取得し終える前に公開を投げてしまう。
        self._getter = getter or http_get
        self._sleep = sleeper or time.sleep

    # ------------------------------------------------------------ 検査

    def preflight(
        self,
        image_url: str,
        caption: str,
        plan_post: dict[str, Any] | None = None,
        consent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """公開前の検査。止める理由と、人が見るべき警告を分けて返す。

        blockers は確認フラグでも上書きできない。
        warnings は人が見て判断する。
        """
        blockers: list[str] = []
        warnings: list[str] = []

        if not image_url.strip():
            blockers.append("画像の URL が指定されていません。")
        elif not image_url.strip().lower().startswith("https://"):
            blockers.append(
                "画像は公開された HTTPS の URL である必要があります。"
                "Instagram のサーバが取りに行くため、手元の PC のファイルは投稿できません。"
            )

        if not caption.strip():
            warnings.append("キャプションが空です。")
        elif len(caption) > CAPTION_MAX:
            blockers.append(
                f"キャプションが {len(caption)} 文字で、上限の {CAPTION_MAX} 文字を超えています。"
            )

        if plan_post is not None:
            if plan_post.get("status") == "投稿済":
                blockers.append(
                    f"この投稿は既に「投稿済」です(計画 {plan_post['id']})。"
                    "二重投稿を防ぐため公開しません。"
                )
            if not plan_post.get("assets_ready"):
                warnings.append("計画上、素材が未確認のままです。")

        if consent is not None and not consent.get("publishable"):
            blockers.append(
                f"題材の案件は掲載許諾が「{consent.get('consent_status')}」です。"
                f"{consent.get('guidance', '')}"
            )
        conditions = (consent or {}).get("conditions")
        if conditions:
            warnings.append(f"掲載条件があります: {conditions}")

        review = review_copy(caption) if caption.strip() else {"count": 0, "flags": []}
        for flag in review["flags"]:
            warnings.append(f"[{flag['category']}] {flag['phrase']} — {flag['reason']}")

        return {
            "image_url": image_url,
            "caption": caption,
            "caption_length": len(caption),
            "blockers": blockers,
            "warnings": warnings,
            "copy_findings": review["count"],
            "can_publish": not blockers,
            "image_requirements": list(IMAGE_REQUIREMENTS),
            "note": "blockers は確認フラグでも上書きできない。"
            "warnings は人が見て判断する。公開すると取り消せない。",
        }

    # ------------------------------------------------------------ 実行

    def publish(
        self,
        image_url: str,
        caption: str,
        confirm: bool = False,
        plan_post: dict[str, Any] | None = None,
        consent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """公開する。`confirm=True` がなければ下見だけを返す。"""
        check = self.preflight(image_url, caption, plan_post, consent)
        if not confirm:
            return {"published": False, "dry_run": True, **check}
        if check["blockers"]:
            raise PublishError(
                "公開を中止しました:\n" + "\n".join(f"- {b}" for b in check["blockers"])
            )

        container = self._post(
            f"{BASE_URL}/me/media",
            {"image_url": image_url, "caption": caption,
             "access_token": self.credentials.access_token},
        )
        container_id = container.get("id")
        if not container_id:
            raise PublishError("コンテナの作成に失敗しました(ID が返りませんでした)")

        self._await_container(container_id)

        result = self._post(
            f"{BASE_URL}/me/media_publish",
            {"creation_id": container_id,
             "access_token": self.credentials.access_token},
        )
        media_id = result.get("id")
        if not media_id:
            raise PublishError("公開に失敗しました(投稿 ID が返りませんでした)")

        return {
            "published": True,
            "dry_run": False,
            "media_id": media_id,
            "container_id": container_id,
            "warnings": check["warnings"],
        }

    def _await_container(self, container_id: str) -> None:
        """コンテナの処理完了を待つ。Instagram が画像を取得し終えるまで。"""
        waited = 0
        while waited < CONTAINER_TIMEOUT_SECONDS:
            status = self._getter(
                f"{BASE_URL}/{container_id}?"
                + urllib.parse.urlencode({
                    "fields": "status_code,status",
                    "access_token": self.credentials.access_token,
                })
            )
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise PublishError(
                    "Instagram が画像を取得できませんでした: "
                    + str(status.get("status", ""))
                    + "。画像の URL が公開されているか、"
                    + "、".join(IMAGE_REQUIREMENTS)
                    + " を満たすか確認してください。"
                )
            self._sleep(CONTAINER_POLL_SECONDS)
            waited += CONTAINER_POLL_SECONDS
        raise PublishError(
            f"{CONTAINER_TIMEOUT_SECONDS} 秒待っても画像の処理が終わりませんでした。"
            "画像のサイズを小さくするか、時間をおいて再実行してください。"
        )
