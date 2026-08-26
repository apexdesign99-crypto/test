"""FastAPI アプリケーション本体。

画面（`static/index.html`）と JSON API を提供する。算定ロジックは持たず、
`ai_land_design` パッケージのパイプラインを呼び出して結果を返すだけに留める。
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ai_land_design import (
    __version__,
    application as application_module,
    compliance as compliance_module,
    drawings as drawings_module,
    exterior,
    pdf_report as pdf_module,
    structure as structure_module,
    layout,
    pipeline,
)
from ai_land_design.bim import to_ifc
from ai_land_design.cost import GRADE_FACTOR
from ai_land_design.documents import to_markdown as permit_markdown
from ai_land_design.models import Direction, FireZone, Site, Structure, UseDistrict
from ai_land_design.pdfkit import FontError
from ai_land_design.sources.gis import LocalGisProvider
from ai_land_design.sources.http import ApiError, NetworkUnavailable
from ai_land_design.sources.realestate import LocalRealEstateProvider
from ai_land_design.sources.geocoding import GsiGeocoder
from ai_land_design.sources.hazard_lookup import HazardTileProvider, HttpTileSource
from ai_land_design.sources.resolve import build_resolver
from ai_land_design.sources.zoning_lookup import GeoJsonZoningProvider, ReinfolibZoningProvider

from . import settings_store
from .schemas import AnalyzeRequest, ResolveRequest, SettingsIn

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
        "provenance": site.provenance,
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


def _resolver_from_settings():
    """保存済みの設定（画面で登録した内容）でリゾルバを組み立てる。"""
    settings, _ = settings_store.load()
    return build_resolver(
        live=settings.live,
        zoning_geojson=settings.zoning_geojson or None,
        geocode_table=settings.geocode_table or None,
        geocode_cache=settings.geocode_cache or None,
        reinfolib_key=settings.reinfolib_api_key or None,
        zoning_api=settings.zoning_api,
    )


#: 接続テストに使う地点（東京都千代田区霞が関）
TEST_POINT = (35.6759, 139.7509)
TEST_ADDRESS = "東京都千代田区霞が関1-1-1"
#: 接続テストは待たせないよう、短いタイムアウトで1回だけ試す
TEST_TIMEOUT = 8.0
TEST_RETRIES = 0


def _describe_api_error(error: ApiError) -> str:
    if error.status in (401, 403):
        return (
            f"認証エラー（{error.status}）。API キーが誤っているか、この API の利用申請が"
            "まだ承認されていない可能性があります。"
        )
    if error.status == 404:
        return f"エンドポイントが見つかりません（404）。API 名が正しいか確認してください。"
    if error.status == 429:
        return "レート制限（429）。しばらく待ってから再試行してください。"
    return f"エラー応答（{error.status}）"


@app.get("/api/settings")
def read_settings() -> Dict[str, Any]:
    """保存済みの設定（API キーはマスク）。"""
    settings, origins = settings_store.load()
    view = settings.public_view(origins)
    view["config_path"] = str(settings_store.config_path().resolve())
    view["config_exists"] = settings_store.config_path().exists()
    try:
        _, notes = _resolver_from_settings()
        view["ready"] = True
        view["sources"] = notes
    except ValueError as error:
        view["ready"] = False
        view["sources"] = []
        view["reason"] = str(error)
    return view


@app.put("/api/settings")
def write_settings(payload: SettingsIn) -> Dict[str, Any]:
    """設定を保存する。API キーが空文字なら既存の値を保持する。"""
    changes = payload.model_dump(exclude_none=True)
    for key in ("zoning_geojson", "geocode_table", "geocode_cache"):
        value = changes.get(key)
        if value and not Path(value).expanduser().exists():
            raise HTTPException(status_code=422, detail=f"ファイルが見つかりません: {value}")
        if value:
            changes[key] = str(Path(value).expanduser())
    settings_store.update(changes)
    return read_settings()


@app.delete("/api/settings/api-key")
def delete_api_key() -> Dict[str, Any]:
    """保存済みの API キーを削除する。"""
    settings_store.clear_api_key()
    return read_settings()


@app.post("/api/settings/test")
def test_settings() -> Dict[str, Any]:
    """登録した設定で各データソースに実際に接続してみる。"""
    settings, _ = settings_store.load()
    results: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, skipped: bool = False) -> None:
        results.append({"name": name, "ok": ok, "detail": detail, "skipped": skipped})

    # 1. ジオコーディング
    if settings.geocode_table:
        add("ジオコーディング（ローカル辞書）", True, f"辞書を使用: {settings.geocode_table}")
    elif settings.live:
        try:
            point = GsiGeocoder(timeout=TEST_TIMEOUT, retries=TEST_RETRIES).geocode(TEST_ADDRESS)
            if point:
                add("ジオコーディング（国土地理院）", True, f"{TEST_ADDRESS} → {point.lat:.4f}, {point.lon:.4f}")
            else:
                add("ジオコーディング（国土地理院）", False, "応答は得られましたが該当なしでした")
        except NetworkUnavailable as error:
            add("ジオコーディング（国土地理院）", False, f"到達できません: {error}")
        except ApiError as error:
            add("ジオコーディング（国土地理院）", False, _describe_api_error(error))
    else:
        add("ジオコーディング", False, "外部 API が無効で、ローカル辞書も未設定です", skipped=True)

    # 2. 用途地域
    if settings.zoning_geojson:
        try:
            provider = GeoJsonZoningProvider(settings.zoning_geojson)
            add(
                "用途地域（国土数値情報 A29）",
                bool(provider.features),
                f"{len(provider.features)} 件のポリゴンを読み込みました",
            )
        except Exception as error:  # ファイル破損・形式違い
            add("用途地域（国土数値情報 A29）", False, f"読み込めません: {error}")
    elif settings.reinfolib_api_key and settings.live:
        try:
            provider = ReinfolibZoningProvider(
                settings.reinfolib_api_key,
                api_name=settings.zoning_api,
                timeout=TEST_TIMEOUT,
                retries=TEST_RETRIES,
            )
            record = provider.zoning_at(*TEST_POINT)
            if record:
                add(
                    f"用途地域（不動産情報ライブラリ {settings.zoning_api}）",
                    True,
                    f"霞が関の判定: {record.use_district.value}",
                )
            else:
                add(
                    f"用途地域（不動産情報ライブラリ {settings.zoning_api}）",
                    True,
                    "API は応答しましたが、テスト地点に用途地域のポリゴンがありませんでした"
                    "（キーは有効です）",
                )
        except ApiError as error:
            add(f"用途地域（不動産情報ライブラリ {settings.zoning_api}）", False, _describe_api_error(error))
        except NetworkUnavailable as error:
            add(
                f"用途地域（不動産情報ライブラリ {settings.zoning_api}）",
                False,
                f"到達できません: {error}",
            )
    elif settings.reinfolib_api_key:
        add("用途地域（不動産情報ライブラリ）", False, "API キーはありますが「外部 API を利用」が無効です", skipped=True)
    else:
        add("用途地域", False, "A29 の GeoJSON も API キーも未設定です", skipped=True)

    # 3. ハザード
    if settings.live:
        try:
            result = HazardTileProvider(
                HttpTileSource(timeout=TEST_TIMEOUT, retries=TEST_RETRIES)
            ).flood_depth(*TEST_POINT)
            if result.determined:
                add(
                    "ハザード（ハザードマップポータル）",
                    True,
                    f"タイル取得 OK（{result.note or '判定済み'}）",
                )
            else:
                add("ハザード（ハザードマップポータル）", False, result.error or result.note)
        except NetworkUnavailable as error:
            add("ハザード（ハザードマップポータル）", False, f"到達できません: {error}")
        except Exception as error:
            add("ハザード（ハザードマップポータル）", False, str(error))
    else:
        add("ハザード", False, "「外部 API を利用」が無効です", skipped=True)

    return {
        "results": results,
        "ok": all(r["ok"] for r in results if not r["skipped"]) and any(r["ok"] for r in results),
    }


@app.post("/api/resolve")
def resolve(request: ResolveRequest) -> Dict[str, Any]:
    """住所から敷地条件（用途地域・建蔽率・容積率・道路・ハザード）を組み立てる。

    データソースはサーバ側の環境変数で設定する。

        AI_LAND_DESIGN_LIVE=1              国土地理院・OSM・ハザードマップを使う
        AI_LAND_DESIGN_ZONING_GEOJSON=...  国土数値情報 A29（用途地域）の GeoJSON
        AI_LAND_DESIGN_GEOCODE_TABLE=...   住所→緯度経度のローカル辞書
        AI_LAND_DESIGN_GEOCODE_CACHE=...   ジオコーディング結果のキャッシュ
        REINFOLIB_API_KEY=...              不動産情報ライブラリ API キー
    """
    try:
        resolver, notes = _resolver_from_settings()
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        resolved = resolver.resolve(
            request.address,
            area_m2=request.area_m2,
            road_width_m=request.road_width_m,
            frontage_m=request.frontage_m,
            land_price_jpy=request.land_price_jpy,
            station_distance_m=request.station_distance_m,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except NetworkUnavailable as error:
        raise HTTPException(status_code=502, detail=f"外部 API に到達できません: {error}") from error
    except ApiError as error:
        raise HTTPException(status_code=502, detail=f"外部 API がエラーを返しました: {error}") from error

    payload = resolved.to_dict()
    payload["request"] = _site_to_request(resolved.site)
    payload["sources"] = notes
    return payload


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest) -> JSONResponse:
    """全工程を実行し、レポートと図面（SVG）を返す。"""
    result = _run(request)
    payload: Dict[str, Any] = result.to_dict()
    payload["markdown"] = pipeline.to_markdown(result)
    payload["drawings"] = {"plans": [], "exterior": None}

    if result.building and result.building.floors:
        site, envelope, building = result.site, result.envelope, result.building
        payload["drawings"]["plans"] = [
            {"storey": floor.storey, "svg": layout.to_svg(site, building, floor.storey)}
            for floor in building.floors
        ]
        payload["drawings"]["exterior"] = exterior.to_svg(building)
        payload["drawings"]["site_plan"] = drawings_module.site_plan_svg(site, building, envelope)
        payload["drawings"]["elevations"] = [
            {"facade": facade, "svg": svg}
            for facade, svg in drawings_module.all_elevations_svg(site, building).items()
        ]
        payload["drawings"]["section"] = drawings_module.section_svg(site, building)
        payload["drawings"]["area_calculation"] = drawings_module.area_calculation_svg(
            site, building
        )
        payload["permit_markdown"] = permit_markdown(site, envelope, building)
        payload["application_markdown"] = application_module.to_markdown(
            site, envelope, building, result.options.application
        )
        if result.code_check:
            payload["compliance_markdown"] = compliance_module.to_markdown(result.code_check)
        if result.wall_quantity:
            payload["structure_markdown"] = structure_module.to_markdown(result.wall_quantity)
    return JSONResponse(payload)


#: 日本語ファイル名の ASCII 代替（Content-Disposition の fallback 用）
_ASCII_ALIAS = {"南": "south", "北": "north", "東": "east", "西": "west"}


def content_disposition(filename: str) -> str:
    """Content-Disposition ヘッダ。

    HTTP ヘッダは latin-1 しか通らないため、日本語を含むファイル名は
    RFC 5987 の `filename*` で渡し、`filename` には ASCII 代替を入れる。
    """
    ascii_name = filename
    for japanese, alias in _ASCII_ALIAS.items():
        ascii_name = ascii_name.replace(japanese, alias)
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii") or "download"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


SVG = "image/svg+xml"
MARKDOWN = "text/markdown; charset=utf-8"

EXPORT_FORMATS = {
    "ifc": ("model.ifc", "application/x-step"),
    "obj": ("massing.obj", "text/plain; charset=utf-8"),
    "report-md": ("report.md", MARKDOWN),
    "report-json": ("report.json", "application/json"),
    "permit-md": ("permit.md", MARKDOWN),
    "plan-svg": ("plan.svg", SVG),
    "exterior-svg": ("exterior.svg", SVG),
    "site-plan-svg": ("site_plan.svg", SVG),
    "elevation-svg": ("elevation.svg", SVG),
    "section-svg": ("section.svg", SVG),
    "area-svg": ("area_calculation.svg", SVG),
    "application-html": ("application.html", "text/html; charset=utf-8"),
    "application-md": ("application.md", MARKDOWN),
    "compliance-md": ("compliance.md", MARKDOWN),
    "structure-md": ("wall_quantity.md", MARKDOWN),
    "pdf": ("申請図書.pdf", "application/pdf"),
    "compliance-json": ("compliance.json", "application/json"),
}


@app.post("/api/export/{fmt}")
def export(
    fmt: str,
    request: AnalyzeRequest,
    storey: int = Query(default=1, ge=1, le=10, description="plan-svg のときの階数"),
    facade: str = Query(default="南", description="elevation-svg のときの方位"),
) -> Response:
    """成果物を1ファイルとしてダウンロードする。"""
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=404, detail=f"未対応の形式: {fmt}（{', '.join(EXPORT_FORMATS)}）"
        )
    result = _run(request)
    filename, media_type = EXPORT_FORMATS[fmt]

    if fmt == "pdf":
        try:
            body = pdf_module.build(result)
        except FontError as error:
            raise HTTPException(
                status_code=503,
                detail=f"PDF を生成できません: {error}",
            ) from error
        prefix = result.site.site_id or "site"
        return Response(
            content=body,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition(f"{prefix}_申請図書.pdf")},
        )

    if fmt == "report-md":
        body = pipeline.to_markdown(result)
    elif fmt == "report-json":
        body = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    else:
        if not result.building or not result.building.floors:
            raise HTTPException(
                status_code=409, detail="建築可能判定で不可となったため、この成果物は生成できません"
            )
        site, envelope, building = result.site, result.envelope, result.building
        if fmt == "ifc":
            body = to_ifc(
                site, building, project_name=result.options.project_name, envelope=envelope
            )
        elif fmt == "obj":
            body = exterior.build_massing(building).to_obj(site.site_id)
        elif fmt == "permit-md":
            body = permit_markdown(site, envelope, building)
        elif fmt == "exterior-svg":
            body = exterior.to_svg(building)
        elif fmt == "site-plan-svg":
            body = drawings_module.site_plan_svg(site, building, envelope)
        elif fmt == "section-svg":
            body = drawings_module.section_svg(site, building)
        elif fmt == "area-svg":
            body = drawings_module.area_calculation_svg(site, building)
        elif fmt == "elevation-svg":
            direction = next((d for d in Direction if d.value == facade), None)
            if direction is None:
                raise HTTPException(status_code=422, detail=f"未知の方位: {facade}")
            body = drawings_module.elevation_svg(site, building, direction)
            filename = f"elevation_{facade}.svg"
        elif fmt == "application-html":
            body = application_module.to_html(
                site, envelope, building, result.options.application, result.code_check,
                drawings_module.all_drawings(site, building, envelope),
            )
        elif fmt == "application-md":
            body = application_module.to_markdown(
                site, envelope, building, result.options.application
            )
        elif fmt == "compliance-md":
            if result.code_check is None:
                raise HTTPException(status_code=409, detail="法適合チェックがありません")
            body = compliance_module.to_markdown(result.code_check)
        elif fmt == "structure-md":
            if result.wall_quantity is None:
                raise HTTPException(
                    status_code=409, detail="壁量計算は木造の計画でのみ出力できます"
                )
            body = structure_module.to_markdown(result.wall_quantity)
        elif fmt == "compliance-json":
            if result.code_check is None:
                raise HTTPException(status_code=409, detail="法適合チェックがありません")
            body = json.dumps(result.code_check.to_dict(), ensure_ascii=False, indent=2)
        else:  # plan-svg
            if storey > len(building.floors):
                raise HTTPException(status_code=404, detail=f"{storey}階は存在しません")
            body = layout.to_svg(site, building, storey)
            filename = f"plan_{storey}f.svg"

    prefix = result.site.site_id or "site"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(f"{prefix}_{filename}")},
    )


@app.post("/api/package")
def package(request: AnalyzeRequest) -> Response:
    """確認申請パッケージ（図面・IFC・申請書・チェック・レポート）を ZIP で返す。"""
    result = _run(request)
    files = pipeline.application_package(result)
    buffer = io.BytesIO()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    root = f"{result.site.site_id or 'site'}_{stamp}"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"{root}/{name}", content)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{root}.zip")},
    )


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/settings", include_in_schema=False)
def settings_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "settings.html").read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
