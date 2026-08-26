"""AI社員の実行設定。

モデル ID やトークン上限など、全社共通の既定値をここに集約する。
"""

from __future__ import annotations

import os
from pathlib import Path

# 既定の思考エンジン。Claude Opus 5 を使う。
DEFAULT_MODEL = "claude-opus-5"

# Opus 5 は refusal(拒否)で HTTP 200 / stop_reason="refusal" を返しうるため、
# サーバサイドの自動フォールバックを既定で有効にする。
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# ストリーミング前提なので出力上限は広めに取る。
MAX_TOKENS = 64_000

# 思考の深さ。低: low / 標準: high / 難所: xhigh。
DEFAULT_EFFORT = "high"

# 1 リクエスト内でツールを呼び返す最大回数(暴走防止)。
MAX_TOOL_ITERATIONS = 24

# pause_turn(サーバツールの中断)からの再開回数上限。
MAX_PAUSE_RESTARTS = 5

# 会社(ワークスペース群)の置き場所。
ENV_HOME = "AI_EMPLOYEE_HOME"
DEFAULT_HOME = "ai-office"


def office_root() -> Path:
    """全社員のデータを格納するルートディレクトリを返す。"""
    return Path(os.environ.get(ENV_HOME, DEFAULT_HOME)).expanduser()
