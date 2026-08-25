# AI LAND DESIGN

土地の情報から、診断 → 建築可能判定 → 間取り → 事業費 → BIM(IFC) → **確認申請図書一式**までを
一気通貫で生成するパイプラインと Web アプリ。算定エンジンは依存ライブラリなし
（Python 3.10+ 標準ライブラリのみ）、Web アプリのみ FastAPI を使う。

敷地条件を入力すると、次の申請パッケージが ZIP で得られる。

| 成果物 | 内容 |
| --- | --- |
| 図面 | 配置図・各階平面図・立面図4面・断面図・求積図（SVG） |
| BIM | IFC4（IfcSpace / IfcWindow / IfcDoor / Pset・数量つき） |
| 法適合チェック | 集団規定・単体規定を条文つきで自動判定 |
| 確認申請 記載事項 | 第一面〜第五面のデータシート（印刷用 HTML / Markdown） |
| 事業性レポート | 診断スコア・建築費・総事業費 |

```
                  AI LAND DESIGN
                       │
          ┌────────────┴────────────┐
          ↓                         ↓
      不動産API                  GIS・地図
          │                         │
          └──────────┬──────────────┘
                     ↓
                AI土地診断
                     ↓
              建築可能判定
                     ↓
          ┌──────────┴──────────┐
          ↓                     ↓
       AI間取り              建築費
          ↓                     ↓
       3D外観                 総事業費
          └──────────┬──────────┘
                     ↓
                  BIM / IFC
                     ↓
             実施設計・確認申請
```

## Web アプリ

ブラウザから敷地条件を入力して、診断・判定・間取り・外観・事業費を画面で確認し、
IFC やレポートをダウンロードできる。

```bash
pip install -r requirements.txt
uvicorn webapp.main:app --reload      # http://127.0.0.1:8000
```

画面は左が入力フォーム（サンプル読込・敷地・用途地域・前面道路・ハザード・建築計画）、
右が結果（診断スコア、建築可能判定、平面図／3D外観、建築費・総事業費、ダウンロード）。
API ドキュメント（OpenAPI）は `/docs` で確認できる。

| エンドポイント | 内容 |
| --- | --- |
| `GET /api/meta` | 用途地域・防火地域・構造などフォームの選択肢と既定値 |
| `GET /api/samples` | サンプル敷地（フォーム初期値としてそのまま投入できる形） |
| `GET /api/listings?address=` | 売地情報の検索と周辺相場（坪単価中央値） |
| `POST /api/analyze` | 全工程を実行し、レポート・図面 SVG を含む結果を返す |
| `POST /api/analyze` の応答 | 図面 SVG（配置図・平面図・立面図4面・断面図・求積図）と法適合チェックを含む |
| `POST /api/package` | **申請パッケージ一式を ZIP で取得** |
| `POST /api/export/{fmt}` | 個別ダウンロード（`ifc` / `site-plan-svg` / `plan-svg` / `elevation-svg?facade=南` / `section-svg` / `area-svg` / `application-html` / `application-md` / `compliance-md` / `compliance-json` / `obj` / `report-md` / `report-json` / `permit-md`） |

`POST /api/analyze` のリクエスト例:

```json
{
  "address": "東京都世田谷区代田1-1-1",
  "width_m": 14, "depth_m": 16,
  "land_price_jpy": 95000000, "station_distance_m": 640,
  "zoning": {"use_district": "第一種住居地域", "building_coverage_ratio": 0.6, "floor_area_ratio": 2.0},
  "roads": [{"width_m": 6.0, "direction": "南", "frontage_m": 14.0}],
  "options": {"household_size": 4, "structure": "木造", "grade": "標準"},
  "application": {
    "owner": {"name": "山田 太郎", "address": "東京都世田谷区代田1-1-1"},
    "designer": {"name": "設計 花子", "qualification": "一級建築士", "office": "〇〇設計事務所"},
    "start_date": "2026-10-01", "completion_date": "2027-03-31"
  }
}
```

敷地形状は `width_m` / `depth_m`（矩形）か `polygon`（頂点座標）のどちらかで指定する。

## コマンドライン

```bash
# 売地情報の検索と相場集計（不動産API層）
python3 -m ai_land_design listings --input samples/listings.json --address 世田谷

# AI 土地診断のみ
python3 -m ai_land_design diagnose --input samples/sites.json --site setagaya \
    --listings samples/listings.json --market-area 世田谷

# 全工程を実行して成果物を出力
python3 -m ai_land_design run --input samples/sites.json --site setagaya \
    --listings samples/listings.json --market-area 世田谷 --out out/
```

`--out` を付けると次のファイルが生成される。

| ファイル | 内容 |
| --- | --- |
| `report.md` / `report.json` | 診断・判定・間取り・建築費・総事業費のレポート |
| `plan_1f.svg`, `plan_2f.svg` … | 各階の平面図（AI間取り） |
| `exterior.svg` | 外観アイソメ図（3D外観） |
| `massing.obj` | 3D マッシング（Wavefront OBJ） |
| `model.ifc` | BIM モデル（IFC4 / STEP） |
| `permit.md` | 確認申請の申請概要・手続き・図書チェックリスト |

Python から使う場合:

```python
from ai_land_design import Options, Structure, run, write_outputs
from ai_land_design.sources.gis import LocalGisProvider

site = LocalGisProvider("samples/sites.json").site_for("setagaya")
result = run(site, Options(household_size=4, structure=Structure.WOOD, grade="標準"))

print(result.diagnosis.rank, result.cost.project_total_jpy)
write_outputs(result, "out/")
```

## 確認申請に向けた作り込み

**間取りが図面として成立する条件を満たす。**

- 全寸法を 910mm グリッド（半間）に載せる
- 階段・ホール・便所を動線コアにまとめ、**階段位置を全階で完全に一致**させる
- 居室が必ず外壁に面するよう分割し、各室に開口部（窓・玄関ドア）を生成する
- 水回りはユニットバスが納まる 1.82m 幅の帯にまとめる

**法適合を条文つきで自動判定する**（`compliance.py`）。

| 区分 | 判定項目 |
| --- | --- |
| 集団規定 | 用途制限（法48条）／接道義務（法43条）／建蔽率（法53条）／容積率（法52条）／絶対高さ（法55条）／道路・隣地・北側斜線（法56条）／外壁後退（法54条）／防火地域の構造制限（法61条） |
| 単体規定 | 居室の採光 1/7（法28条1項・令19条）／換気 1/20（法28条2項）／天井高 2.1m（令21条）／階段の寸法（令23条）／シックハウス（法28条の2） |
| 対象外 | 構造計算・省エネ計算・日影図・天空率は「要確認」として明示 |

斜線制限は**実際の建物の後退距離**から再計算し、階段は生成された階段室の寸法から
蹴上・踏面・段数を割り出して判定する。

**IFC は BIM 申請で参照される属性を持たせる。**

- IfcOpeningElement ─ IfcWindow / IfcDoor（IfcRelVoidsElement / IfcRelFillsElement で壁と関連付け）
- Pset_BuildingCommon / Pset_WallCommon / Pset_SpaceCommon / Pset_WindowCommon
- `Pset_JP_ConfirmationApplication`（用途地域・建蔽率・容積率・最高の高さと各限度）
- IfcElementQuantity（各室の床面積）

出力は ifcopenshell で読み込み・ジオメトリ生成まで検証している（`tests/test_bim.py`）。

## 各工程の実装

| 工程 | モジュール | 内容 |
| --- | --- | --- |
| 不動産API | `sources/realestate.py` | 売地情報の検索、坪単価と周辺相場の集計 |
| GIS・地図 | `sources/gis.py` | 敷地ポリゴン・用途地域・前面道路・ハザードの取得 |
| AI土地診断 | `diagnosis.py` | 5 軸（法規／形状／接道／立地／価格）の重み付きスコアとランク |
| 建築可能判定 | `feasibility.py` | 接道義務・セットバック・建蔽率・容積率・各種高さ制限 |
| AI間取り | `layout.py` | 室プログラム決定 → 面積配分 → 再帰分割で部屋を敷き詰め |
| 3D外観 | `exterior.py` | 各階を押し出したマッシング、屋根形状、OBJ / SVG 出力 |
| 建築費・総事業費 | `cost.py` | 坪単価積み上げ、付帯・設計・諸費用・税を含む事業費 |
| BIM / IFC | `bim/ifc.py` | IFC4 の STEP ファイル生成（Storey / Slab / Wall / Space） |
| 法適合チェック | `compliance.py` | 集団規定・単体規定の自動判定（条文・要求値・実績値） |
| 申請図面 | `drawings.py`, `svgkit.py` | 配置図・立面図4面・断面図・求積図 |
| 確認申請書 | `application.py` | 第一面〜第五面の記載事項、印刷用 HTML |
| 実施設計・確認申請 | `documents.py` | 申請概要、必要手続きの判定、設計図書チェックリスト |
| パイプライン | `pipeline.py` | 上記の受け渡しとレポート生成 |
| 画面・API | `webapp/` | FastAPI（`main.py` / `schemas.py`）と画面（`static/`） |

### 建築可能判定でみている規制

- **接道義務**（法43条）: 幅員 4m 以上の道路に 2m 以上接するか。42条2項道路は中心後退 2m を想定
- **建蔽率**（法53条）: 角地緩和 +10%、防火地域内耐火建築物 +10%、指定 80% + 防火地域は制限なし
- **容積率**（法52条）: 前面道路幅員 12m 未満のとき、住居系 ×0.4 / その他 ×0.6 と指定容積率の小さい方
- **高さ**（法55条・56条）: 絶対高さ、道路斜線（勾配・後退緩和・適用距離）、隣地斜線、北側斜線

判定結果は建築可能ボリューム（建築面積上限・延べ面積上限・高さ上限・階数）として後段に渡る。
生成した建物案は `documents.compliance_check()` で上限内かを再検証している。

### AI 土地診断のスコアリング

ブラックボックスではなく、評価軸ごとの決定的なスコア関数の集合として実装している。
各項目のスコア・重み・根拠コメントを返すので、点数の理由を常に説明できる。
重み（`diagnosis.WEIGHTS`）と料率（`cost.Rates`）は事業者ごとの方針・実績値に差し替えて使う。

## 外部 API への接続

`sources/` の各 Provider は Protocol とローカル実装に分かれている。
実 API（不動産情報ライブラリ、都市計画 GIS、自治体オープンデータ等）を使う場合は、
`search()` / `feature_for()` を実装したクラスを差し替えれば、後段の工程はそのまま動く。
同梱の `LocalRealEstateProvider` / `LocalGisProvider` は JSON を読むだけのオフライン実装で、
サンプルとテストに使っている。

## テスト

```bash
python3 -m unittest discover -s tests -t .
```

Web アプリの API テストは FastAPI と httpx が、IFC の検証テストは ifcopenshell が必要
（未インストールならスキップされる）。

```bash
pip install -r requirements-dev.txt
```

## 制約

本ツールの出力は概算であり、そのまま確認申請に使えるものではない。

- 天空率・日影規制の詳細計算、地区計画・条例・協定による個別規制は未対応
- 構造計算（壁量計算・許容応力度計算）と省エネ計算は行っていない
- 間取りは矩形の外形を前提とした自動生成で、動線・家具配置・意匠は検討していない
- 図面は下書きであり、通り芯・仕上・納まり・矩計図は含まれない
- 確認申請書は正規様式ではなく、様式に転記するためのデータシート
- 北側斜線は棟位置での近似評価。建物形状による厳密な検討は別途必要
- 建築費の単価・料率は目安値。実際の見積は施工者・地域・時期で大きく変動する
- 法適合の最終判断には建築士による設計と、特定行政庁または指定確認検査機関への確認が必要
