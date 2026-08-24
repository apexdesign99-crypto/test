"""FastAPI アプリケーション本体。

画面（`static/index.html`）と JSON API を提供する。算定ロジックは持たず、
`ai_land_design` パッケージのパイプラインを呼び出して結果を返すだけに留める。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ai_land_design import __version__, exterior, layout, pipeline
from ai_land_design.bim import to_ifc
from ai_land_design.cost import GRADE_FACTOR
from ai_land_design.documents import to_markdown as permit_markdown
from ai_land_design.models import Direction, FireZone, Site, Structure, UseDistrict
from ai_land_design.sources.gis import LocalGisProvider
from ai_land_design.sources.realestate import LocalRealEstateProvider

from .schemas import AnalyzeRequest

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR.parent / "samples"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="AI LAND DESIGN",
    version=__version__,
    description="土地診断から建築可能判定・間取り・事業費・BIM(IFC) までを算出する API",
)


def _sample_sites() -> List[Site]:
    path = SAMPLES_DIR / "sites.json"
    return LocalGisProvider(path).all_sites() if path.exists() else []


def _site_to_request(site: Site) -> Dict[str, Any]:
    """サンプル敷地を画面のフォーム初期値に変換する。"""
    return {
        "site_id": site.site_id,
        "address": site.address,
        "polygon": [list(p) for p in site.polygon],
        "zoning": {
            "use_district": site.zoning.use_district.value,
            "building_coverage_ratio": site.zoning.building_coverage_ratio,
            "floor_area_ratio": site.zoning.floor_area_ratio,
            "fire_zone": site.zoning.fire_zone.value,
            "height_limit_m": site.zoning.height_limit_m,
            "wall_setback_m": site.zoning.wall_setback_m,
            "shadow_regulation": site.zoning.shadow_regulation,
            "is_corner_lot": site.zoning.is_corner_lot,
            "scenic_district": site.zoning.scenic_district,
        },
        "roads": [r.to_dict() for r in site.roads],
        "hazard": site.hazard.to_dict(),
        "land_price_jpy": site.land_price_jpy,
        "station_distance_m": site.station_distance_m,
        "note": site.note,
    }


def _run(request: AnalyzeRequest) -> pipeline.ProjectResult:
    try:
        return pipeline.run(request.to_site(), request.to_options())
    except ValueError as error:  # 用途地域名などの変換エラー
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/meta")
def meta() -> Dict[str, Any]:
    """フォームの選択肢。用途地域ごとの標準的な建蔽率・容積率も返す。"""
    return {
        "use_districts": [
            {
                "value": u.value,
                "is_low_rise": u.is_low_rise,
                "allows_dwelling": u.allows_dwelling,
                "default_bcr": 0.5 if u.is_low_rise else (0.8 if u.value == "商業地域" else 0.6),
                "default_far": 1.0 if u.is_low_rise else (5.0 if u.value == "商業地域" else 2.0),
                "default_height_limit_m": 10.0 if u.is_low_rise else None,
            }
            for u in UseDistrict
        ],
        "fire_zones": [f.value for f in FireZone],
        "structures": [s.value for s in Structure],
        "grades": list(GRADE_FACTOR),
        "directions": [d.value for d in Direction],
    }


@app.get("/api/samples")
def samples() -> Dict[str, Any]:
    return {
        "samples": [
            {"id": site.site_id, "label": f"{site.address}（{site.note}）", "request": _site_to_request(site)}
            for site in _sample_sites()
        ]
    }


@app.get("/api/listings")
def listings(
    address: str = Query(default="", description="住所の部分一致"),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """不動産 API 層。売地情報の検索と相場（坪単価中央値）の集計。"""
    path = SAMPLES_DIR / "listings.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="売地データが登録されていません")
    provider = LocalRealEstateProvider(path)
    hits = provider.search(address, limit)
    return {
        "listings": [l.to_dict() for l in hits],
        "median_unit_price_per_tsubo": provider.median_unit_price(address),
        "count": len(hits),
    }


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> JSONResponse:
    """全工程を実行し、レポートと図面（SVG）を返す。"""
    result = _run(request)
    payload: Dict[str, Any] = result.to_dict()
    payload["markdown"] = pipeline.to_markdown(result)
    payload["drawings"] = {"plans": [], "exterior": None}

    if result.building and result.building.floors:
        payload["drawings"]["plans"] = [
            {
                "storey": floor.storey,
                "svg": layout.to_svg(result.site, result.building, floor.storey),
            }
            for floor in result.building.floors
        ]
        payload["drawings"]["exterior"] = exterior.to_svg(result.building)
        payload["permit_markdown"] = permit_markdown(
            result.site, result.envelope, result.building
        )
    return JSONResponse(payload)


EXPORT_FORMATS = {
    "ifc": ("model.ifc", "application/x-step"),
    "obj": ("massing.obj", "text/plain; charset=utf-8"),
    "report-md": ("report.md", "text/markdown; charset=utf-8"),
    "report-json": ("report.json", "application/json"),
    "permit-md": ("permit.md", "text/markdown; charset=utf-8"),
    "plan-svg": ("plan.svg", "image/svg+xml"),
    "exterior-svg": ("exterior.svg", "image/svg+xml"),
}


@app.post("/api/export/{fmt}")
def export(
    fmt: str,
    request: AnalyzeRequest,
    storey: int = Query(default=1, ge=1, le=10, description="plan-svg のときの階数"),
) -> Response:
    """成果物を1ファイルとしてダウンロードする。"""
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=404, detail=f"未対応の形式: {fmt}（{', '.join(EXPORT_FORMATS)}）"
        )
    result = _run(request)
    filename, media_type = EXPORT_FORMATS[fmt]

    if fmt == "report-md":
        body = pipeline.to_markdown(result)
    elif fmt == "report-json":
        body = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        if not result.building or not result.building.floors:
            raise HTTPException(
                status_code=409, detail="建築可能判定で不可となったため、この成果物は生成できません"
            )
        if fmt == "ifc":
            body = to_ifc(result.site, result.building, project_name=result.options.project_name)
        elif fmt == "obj":
            body = exterior.build_massing(result.building).to_obj(result.site.site_id)
        elif fmt == "permit-md":
            body = permit_markdown(result.site, result.envelope, result.building)
        elif fmt == "exterior-svg":
            body = exterior.to_svg(result.building)
        else:  # plan-svg
            if storey > len(result.building.floors):
                raise HTTPException(status_code=404, detail=f"{storey}階は存在しません")
            body = layout.to_svg(result.site, result.building, storey)
            filename = f"plan_{storey}f.svg"

    prefix = result.site.site_id or "site"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{prefix}_{filename}"'},
    )


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
