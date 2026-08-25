"""コマンドラインインタフェース。

    python -m ai_land_design run --input samples/sites.json --site setagaya --out out/
    python -m ai_land_design run --address "東京都世田谷区代田1-1-1" --area 180 --live --out out/
    python -m ai_land_design resolve --address "東京都世田谷区代田1-1-1" --area 180 --live
    python -m ai_land_design diagnose --input samples/sites.json --site setagaya
    python -m ai_land_design listings --input samples/listings.json --address 世田谷
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import pipeline
from .diagnosis import diagnose
from .models import Site, Structure
from .sources.gis import LocalGisProvider
from .sources.http import NetworkUnavailable
from .sources.realestate import LocalRealEstateProvider
from .sources.resolve import ResolvedSite, build_resolver


def _load_site(input_path: str, key: Optional[str]) -> Site:
    provider = LocalGisProvider(input_path)
    sites = provider.all_sites()
    if not sites:
        raise SystemExit(f"敷地データが空です: {input_path}")
    if key:
        site = provider.site_for(key)
        if site is None:
            available = ", ".join(s.site_id for s in sites)
            raise SystemExit(f"敷地 '{key}' が見つかりません。利用可能: {available}")
        return site
    return sites[0]


def _structure(value: str) -> Structure:
    for s in Structure:
        if value in (s.value, s.name, s.name.lower()):
            return s
    raise SystemExit(f"未知の構造: {value}（木造 / 鉄骨造 / 鉄筋コンクリート造）")


def _options(args: argparse.Namespace) -> pipeline.Options:
    market = args.market_price
    if market is None and args.listings:
        provider = LocalRealEstateProvider(args.listings)
        market = provider.median_unit_price(args.market_area or "")
    return pipeline.Options(
        household_size=args.household,
        structure=_structure(args.structure),
        grade=args.grade,
        target_floor_area_m2=args.target_area,
        market_unit_price_per_tsubo=market,
        land_price_jpy=args.land_price,
    )


def _add_resolve_options(parser: argparse.ArgumentParser) -> None:
    """住所から敷地条件を自動取得するためのオプション。"""
    parser.add_argument("--address", help="住所（指定すると公的データから敷地条件を組み立てる）")
    parser.add_argument("--area", type=float, help="敷地面積 [m2]（測量図がない場合の矩形近似に使用）")
    parser.add_argument("--road-width", type=float, help="前面道路の幅員 [m]（実測値で上書き）")
    parser.add_argument("--frontage", type=float, help="接道長 [m]（実測値で上書き）")
    parser.add_argument("--live", action="store_true", help="外部 API（国土地理院・OSM・ハザード）を使う")
    parser.add_argument("--zoning-geojson", help="国土数値情報 A29（用途地域）の GeoJSON")
    parser.add_argument("--geocode-table", help="住所→緯度経度のローカル辞書 JSON")
    parser.add_argument("--geocode-cache", help="ジオコーディング結果のキャッシュ JSON")


def _resolve_site(args: argparse.Namespace) -> ResolvedSite:
    resolver, notes = build_resolver(
        live=args.live or None,
        zoning_geojson=args.zoning_geojson,
        geocode_table=args.geocode_table,
        geocode_cache=args.geocode_cache,
    )
    for note in notes:
        print(f"  {note}", file=sys.stderr)
    try:
        return resolver.resolve(
            args.address,
            area_m2=args.area,
            road_width_m=args.road_width,
            frontage_m=args.frontage,
            land_price_jpy=getattr(args, "land_price", None),
            station_distance_m=None,
            site_id="resolved",
        )
    except NetworkUnavailable as error:
        raise SystemExit(f"外部 API に到達できません: {error}")
    except LookupError as error:
        raise SystemExit(str(error))


def _report_resolution(resolved: ResolvedSite) -> None:
    print("データ出典:", file=sys.stderr)
    for record in resolved.provenance:
        note = f"（{record.note}）" if record.note else ""
        print(f"  {record.field}: {record.value} ← {record.source}{note}", file=sys.stderr)
    if resolved.warnings:
        print("要確認:", file=sys.stderr)
        for warning in resolved.warnings:
            print(f"  - {warning}", file=sys.stderr)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", help="敷地 JSON（GIS 出力形式）。--address と排他")
    parser.add_argument("--site", help="site_id または住所の一部。省略時は先頭の敷地")
    parser.add_argument("--listings", help="不動産 API 相当の売地 JSON（相場比較に使用）")
    parser.add_argument("--market-area", help="相場を集計する住所の接頭辞")
    parser.add_argument("--market-price", type=int, help="相場坪単価を直接指定 [円/坪]")
    parser.add_argument("--land-price", type=int, help="土地取得費 [円]（敷地データを上書き）")
    parser.add_argument("--household", type=int, default=4, help="家族人数（既定 4）")
    parser.add_argument("--structure", default="木造", help="木造 / 鉄骨造 / 鉄筋コンクリート造")
    parser.add_argument("--grade", default="標準", help="ローコスト / 標準 / ハイグレード")
    parser.add_argument("--target-area", type=float, help="目標延床面積 [m2]")
    _add_resolve_options(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_land_design", description="AI LAND DESIGN パイプライン"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="診断から総事業費・IFC までを実行")
    _add_common(run_parser)
    run_parser.add_argument("--out", help="成果物の出力ディレクトリ")
    run_parser.add_argument("--json", action="store_true", help="レポートを JSON で標準出力に出す")

    resolve_parser = sub.add_parser("resolve", help="住所から敷地条件を組み立てる")
    resolve_parser.add_argument("--land-price", type=int, help="土地取得費 [円]")
    resolve_parser.add_argument("--out", help="敷地 JSON の出力先")
    resolve_parser.add_argument("--json", action="store_true", help="JSON で標準出力に出す")
    _add_resolve_options(resolve_parser)

    diag_parser = sub.add_parser("diagnose", help="AI 土地診断のみ実行")
    _add_common(diag_parser)
    diag_parser.add_argument("--json", action="store_true", help="JSON で出力")

    list_parser = sub.add_parser("listings", help="売地情報の検索と相場集計")
    list_parser.add_argument("--input", required=True, help="売地 JSON")
    list_parser.add_argument("--address", default="", help="住所の部分一致")
    list_parser.add_argument("--limit", type=int, default=10)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "resolve":
        if not args.address:
            raise SystemExit("--address を指定してください")
        resolved = _resolve_site(args)
        if args.json:
            print(json.dumps(resolved.to_dict(), ensure_ascii=False, indent=2))
        else:
            site = resolved.site
            print(f"{site.address}")
            print(f"  敷地面積   {site.area_m2:.2f} m²（{site.area_tsubo:.2f} 坪）")
            print(f"  用途地域   {site.zoning.use_district.value}")
            print(
                f"  建蔽率/容積率 {site.zoning.building_coverage_ratio * 100:.0f}% / "
                f"{site.zoning.floor_area_ratio * 100:.0f}%"
            )
            road = site.roads[0]
            print(f"  前面道路   {road.direction.value}側 幅員{road.width_m:.1f}m / 接道{road.frontage_m:.1f}m")
            print(f"  ハザード   浸水{site.hazard.flood_depth_m:.1f}m / 土砂災害 "
                  f"{'あり' if site.hazard.landslide_risk else 'なし'}")
            _report_resolution(resolved)
        if args.out:
            Path(args.out).write_text(
                json.dumps({"sites": [resolved.site.to_dict()]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n敷地 JSON を書き出しました: {args.out}", file=sys.stderr)
        return 0

    if args.command == "listings":
        provider = LocalRealEstateProvider(args.input)
        hits = provider.search(args.address, args.limit)
        if not hits:
            print("該当する売地情報がありません。")
            return 1
        for listing in hits:
            print(
                f"{listing.listing_id:>8}  {listing.address}  "
                f"{listing.price_jpy:,}円  {listing.area_m2:.1f}m2  "
                f"坪単価 {listing.unit_price_per_tsubo:,}円"
            )
        median = provider.median_unit_price(args.address)
        if median:
            print(f"\n相場（坪単価中央値）: {median:,} 円/坪")
        return 0

    if args.address:
        resolved = _resolve_site(args)
        _report_resolution(resolved)
        site = resolved.site
    elif args.input:
        site = _load_site(args.input, args.site)
    else:
        raise SystemExit("--input か --address のいずれかを指定してください")
    options = _options(args)

    if args.command == "diagnose":
        result = diagnose(site, options.market_unit_price_per_tsubo)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
        print(f"{site.address or site.site_id}")
        print(f"総合スコア {result.total_score:.1f} 点（ランク {result.rank}）\n")
        for item in result.items:
            print(f"  {item.name:<12} {item.score:5.1f} (重み {item.weight:.2f})  {item.comment}")
        if result.findings:
            print("")
            for finding in result.findings:
                print(f"  [{finding.level}] {finding.message}")
        return 0

    result = pipeline.run(site, options)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(pipeline.to_markdown(result))

    if args.out:
        written = pipeline.write_outputs(result, args.out)
        print("\n出力ファイル:", file=sys.stderr)
        for path in written:
            print(f"  {path}", file=sys.stderr)

    return 0 if not result.blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
