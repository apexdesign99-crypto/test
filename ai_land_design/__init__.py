"""AI LAND DESIGN — 土地から事業性・間取り・BIM までを一気通貫で算出するパイプライン。

    不動産API / GIS・地図 → AI土地診断 → 建築可能判定
        → AI間取り / 建築費 → 3D外観 / 総事業費 → BIM(IFC) → 実施設計・確認申請

依存は標準ライブラリのみ。使い方は README.md と `python -m ai_land_design --help` を参照。
"""

from .models import (
    Building,
    CostBreakdown,
    Diagnosis,
    Direction,
    Envelope,
    FireZone,
    Hazard,
    Road,
    Site,
    Structure,
    UseDistrict,
    Zoning,
)
from .pipeline import Options, ProjectResult, run, to_markdown, write_outputs

__version__ = "0.1.0"

__all__ = [
    "Building",
    "CostBreakdown",
    "Diagnosis",
    "Direction",
    "Envelope",
    "FireZone",
    "Hazard",
    "Options",
    "ProjectResult",
    "Road",
    "Site",
    "Structure",
    "UseDistrict",
    "Zoning",
    "run",
    "to_markdown",
    "write_outputs",
    "__version__",
]
