"""データソース設定（API キー・ファイルパス）の保存と読み出し。

API キーを扱うため、次を守る。

* 保存先はリポジトリ外に置ける JSON ファイル。既定は `.ai_land_design/settings.json` で
  `.gitignore` 済み。ファイルは所有者のみ読み書き可（0600）にする。
* 画面・API にキーそのものを返さない。常にマスク（末尾4文字のみ）を返す。
* ログに出さない。例外メッセージにも含めない。

設定の優先順位は「保存ファイル > 環境変数」。サーバに環境変数で入れている場合は
そのまま使え、画面から保存した値があればそちらが優先される。
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from ai_land_design.sources.resolve import (
    ENV_GEOCODE_CACHE,
    ENV_GEOCODE_TABLE,
    ENV_LIVE,
    ENV_REINFOLIB_KEY,
    ENV_ZONING_GEOJSON,
)

#: 設定ファイルの場所（環境変数で上書きできる）
ENV_CONFIG_PATH = "AI_LAND_DESIGN_CONFIG"
DEFAULT_CONFIG_PATH = Path(".ai_land_design") / "settings.json"

#: 用途地域 API の既定の API 名（不動産情報ライブラリ）
DEFAULT_ZONING_API = "XKT013"


def config_path() -> Path:
    return Path(os.environ.get(ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH)


def mask(secret: str) -> str:
    """API キーのマスク表記。末尾4文字だけ残す。"""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "*" * len(secret)
    return "*" * (len(secret) - 4) + secret[-4:]


@dataclass
class Settings:
    """データソースの設定。"""

    reinfolib_api_key: str = ""
    zoning_api: str = DEFAULT_ZONING_API
    live: bool = False
    zoning_geojson: str = ""
    geocode_table: str = ""
    geocode_cache: str = ""

    def to_storage(self) -> Dict[str, Any]:
        return {
            "reinfolib_api_key": self.reinfolib_api_key,
            "zoning_api": self.zoning_api,
            "live": self.live,
            "zoning_geojson": self.zoning_geojson,
            "geocode_table": self.geocode_table,
            "geocode_cache": self.geocode_cache,
        }

    def public_view(self, origins: Dict[str, str]) -> Dict[str, Any]:
        """画面に返す形。キーはマスクし、どこから読んだかを添える。"""
        return {
            "reinfolib_api_key_masked": mask(self.reinfolib_api_key),
            "reinfolib_api_key_set": bool(self.reinfolib_api_key),
            "zoning_api": self.zoning_api,
            "live": self.live,
            "zoning_geojson": self.zoning_geojson,
            "geocode_table": self.geocode_table,
            "geocode_cache": self.geocode_cache,
            "origins": origins,
        }


def _from_env() -> Settings:
    return Settings(
        reinfolib_api_key=os.environ.get(ENV_REINFOLIB_KEY, ""),
        live=os.environ.get(ENV_LIVE, "") == "1",
        zoning_geojson=os.environ.get(ENV_ZONING_GEOJSON, "") or "",
        geocode_table=os.environ.get(ENV_GEOCODE_TABLE, "") or "",
        geocode_cache=os.environ.get(ENV_GEOCODE_CACHE, "") or "",
    )


def load() -> tuple[Settings, Dict[str, str]]:
    """有効な設定と、各項目の出どころ（"設定ファイル" / "環境変数" / "未設定"）を返す。"""
    settings = _from_env()
    origins = {
        key: ("環境変数" if value not in ("", False) else "未設定")
        for key, value in settings.to_storage().items()
        if key != "zoning_api"
    }
    origins["zoning_api"] = "既定値"

    path = config_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stored = {}
        for key, value in stored.items():
            if not hasattr(settings, key) or value in ("", None):
                continue
            setattr(settings, key, value)
            origins[key] = "設定ファイル"
    return settings, origins


def save(settings: Settings) -> Path:
    """設定を保存する。ファイルは所有者のみ読み書き可にする。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings.to_storage(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return path


def update(changes: Dict[str, Any], keep_key_if_blank: bool = True) -> tuple[Settings, Path]:
    """保存済みの設定を部分更新する。

    `keep_key_if_blank` が真のとき、API キーが空文字なら既存の値を保持する
    （画面はマスクしか持たないため、未入力＝変更なしとして扱う）。
    """
    current, _ = load()
    for key, value in changes.items():
        if not hasattr(current, key) or value is None:
            continue
        if key == "reinfolib_api_key" and value == "" and keep_key_if_blank:
            continue
        setattr(current, key, value)
    return current, save(current)


def clear_api_key() -> Settings:
    """保存済みの API キーを削除する。"""
    current, _ = load()
    current.reinfolib_api_key = ""
    save(current)
    return current
