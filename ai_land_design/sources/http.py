"""外部 API を叩くための最小 HTTP クライアント（標準ライブラリのみ）。

公的 API（国土地理院・ハザードマップポータル・不動産情報ライブラリ等）への
アクセスをここに集約する。ネットワークが遮断された環境では明示的な例外を投げ、
呼び出し側がオフラインのフォールバックに切り替えられるようにする。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

USER_AGENT = "ai-land-design/0.1 (+https://github.com/apexdesign99-crypto/test)"
DEFAULT_TIMEOUT = 20.0


class NetworkUnavailable(RuntimeError):
    """外部 API に到達できない（遮断・タイムアウト・DNS 失敗など）。"""


class ApiError(RuntimeError):
    """API がエラー応答を返した。"""

    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"{status} {url} {body[:200]}")
        self.status = status
        self.url = url
        self.body = body


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def fetch(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 2,
    backoff: float = 1.5,
) -> Response:
    """GET リクエスト。失敗時は指数バックオフで再試行する。

    到達できない場合は `NetworkUnavailable`、4xx/5xx は `ApiError` を投げる。
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return Response(
                    status=response.status,
                    body=response.read(),
                    content_type=response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code < 500 or attempt == retries:
                raise ApiError(error.code, url, body) from error
            last_error = error
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
            last_error = error
        if attempt < retries:
            time.sleep(backoff ** attempt)

    raise NetworkUnavailable(
        f"{url} に到達できませんでした（{last_error}）。"
        "ネットワークが遮断されている場合は、オフラインのデータソースを指定してください。"
    ) from last_error
