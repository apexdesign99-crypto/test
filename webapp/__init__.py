"""AI LAND DESIGN の Web アプリ（FastAPI）。

`ai_land_design` パッケージの算定エンジンを HTTP API と画面から使えるようにする層。
起動:

    uvicorn webapp.main:app --reload
"""

from .main import app

__all__ = ["app"]
