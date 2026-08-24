"""Web アプリ（FastAPI）の API テスト。

FastAPI が未インストールの環境ではスキップする（算定エンジン本体は標準ライブラリのみで動く）。
"""

import importlib.util
import json
import unittest

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx")

if HAS_FASTAPI:
    from fastapi.testclient import TestClient

    from webapp.main import app

BASE_REQUEST = {
    "address": "東京都世田谷区代田1-1-1",
    "width_m": 14,
    "depth_m": 16,
    "land_price_jpy": 95_000_000,
    "station_distance_m": 640,
    "zoning": {
        "use_district": "第一種住居地域",
        "building_coverage_ratio": 0.6,
        "floor_area_ratio": 2.0,
    },
    "roads": [{"width_m": 6.0, "direction": "南", "frontage_m": 14.0}],
}

BLOCKED_REQUEST = {
    **BASE_REQUEST,
    "roads": [{"width_m": 4.0, "direction": "西", "frontage_m": 1.8}],
}


@unittest.skipUnless(HAS_FASTAPI, "fastapi / httpx が未インストール")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_meta_lists_form_choices(self):
        data = self.client.get("/api/meta").json()
        self.assertEqual(len(data["use_districts"]), 13)
        self.assertIn("木造", data["structures"])
        self.assertIn("標準", data["grades"])
        low_rise = next(u for u in data["use_districts"] if u["value"] == "第一種低層住居専用地域")
        self.assertTrue(low_rise["is_low_rise"])
        self.assertEqual(low_rise["default_height_limit_m"], 10.0)
        industrial = next(u for u in data["use_districts"] if u["value"] == "工業専用地域")
        self.assertFalse(industrial["allows_dwelling"])

    def test_samples_are_form_ready(self):
        samples = self.client.get("/api/samples").json()["samples"]
        self.assertGreaterEqual(len(samples), 4)
        for sample in samples:
            response = self.client.post("/api/analyze", json=sample["request"])
            self.assertEqual(response.status_code, 200, sample["id"])

    def test_listings_search_and_median(self):
        data = self.client.get("/api/listings", params={"address": "世田谷"}).json()
        self.assertGreater(data["count"], 0)
        self.assertGreater(data["median_unit_price_per_tsubo"], 0)

    def test_listings_unknown_area_returns_empty(self):
        data = self.client.get("/api/listings", params={"address": "北海道"}).json()
        self.assertEqual(data["count"], 0)
        self.assertIsNone(data["median_unit_price_per_tsubo"])

    def test_analyze_returns_all_stages(self):
        data = self.client.post("/api/analyze", json=BASE_REQUEST).json()
        self.assertTrue(data["envelope"]["buildable"])
        self.assertIn("summary", data)
        self.assertEqual(len(data["drawings"]["plans"]), data["building"]["storeys"])
        self.assertTrue(data["drawings"]["plans"][0]["svg"].startswith("<svg"))
        self.assertTrue(data["drawings"]["exterior"].startswith("<svg"))
        self.assertIn("## 1. AI 土地診断", data["markdown"])
        self.assertIn("確認申請", data["permit_markdown"])

    def test_analyze_accepts_polygon(self):
        payload = {k: v for k, v in BASE_REQUEST.items() if k not in ("width_m", "depth_m")}
        payload["polygon"] = [[0, 0], [16, 0], [16, 9], [4, 9], [4, 14], [0, 14]]
        data = self.client.post("/api/analyze", json=payload).json()
        self.assertAlmostEqual(data["site"]["area_m2"], 164.0, places=2)

    def test_analyze_blocked_site(self):
        data = self.client.post("/api/analyze", json=BLOCKED_REQUEST).json()
        self.assertFalse(data["envelope"]["buildable"])
        self.assertIsNone(data["building"])
        self.assertEqual(data["drawings"]["plans"], [])

    def test_options_flow_through(self):
        payload = {**BASE_REQUEST, "options": {"household_size": 6, "structure": "鉄筋コンクリート造"}}
        data = self.client.post("/api/analyze", json=payload).json()
        self.assertEqual(data["building"]["structure"], "鉄筋コンクリート造")
        self.assertEqual(data["options"]["household_size"], 6)

    def test_shape_is_required(self):
        response = self.client.post("/api/analyze", json={"address": "テスト"})
        self.assertEqual(response.status_code, 422)

    def test_polygon_needs_three_points(self):
        payload = {k: v for k, v in BASE_REQUEST.items() if k not in ("width_m", "depth_m")}
        payload["polygon"] = [[0, 0], [10, 0]]
        self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 422)

    def test_unknown_use_district_is_rejected(self):
        payload = json.loads(json.dumps(BASE_REQUEST))
        payload["zoning"]["use_district"] = "架空地域"
        self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 422)

    def test_out_of_range_values_are_rejected(self):
        payload = json.loads(json.dumps(BASE_REQUEST))
        payload["zoning"]["building_coverage_ratio"] = 1.5
        self.assertEqual(self.client.post("/api/analyze", json=payload).status_code, 422)

    def test_export_formats(self):
        expected = {
            "ifc": "ISO-10303-21;",
            "obj": "# ",
            "report-md": "# AI LAND DESIGN",
            "permit-md": "# 確認申請",
            "plan-svg": "<svg",
            "exterior-svg": "<svg",
        }
        for fmt, head in expected.items():
            response = self.client.post(f"/api/export/{fmt}", json=BASE_REQUEST)
            self.assertEqual(response.status_code, 200, fmt)
            self.assertTrue(response.text.startswith(head), fmt)
            self.assertIn("attachment", response.headers["content-disposition"])

    def test_export_report_json(self):
        response = self.client.post("/api/export/report-json", json=BASE_REQUEST)
        self.assertEqual(response.status_code, 200)
        self.assertIn("summary", json.loads(response.text))

    def test_export_plan_svg_storey(self):
        ok = self.client.post("/api/export/plan-svg?storey=2", json=BASE_REQUEST)
        self.assertEqual(ok.status_code, 200)
        self.assertIn("plan_2f.svg", ok.headers["content-disposition"])
        missing = self.client.post("/api/export/plan-svg?storey=5", json=BASE_REQUEST)
        self.assertEqual(missing.status_code, 404)

    def test_export_unknown_format(self):
        self.assertEqual(self.client.post("/api/export/dxf", json=BASE_REQUEST).status_code, 404)

    def test_export_blocked_site_conflicts(self):
        response = self.client.post("/api/export/ifc", json=BLOCKED_REQUEST)
        self.assertEqual(response.status_code, 409)
        # レポートは建築不可でも取得できる
        self.assertEqual(
            self.client.post("/api/export/report-md", json=BLOCKED_REQUEST).status_code, 200
        )

    def test_index_and_static_assets(self):
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("AI LAND DESIGN", index.text)
        for asset in ("/static/styles.css", "/static/app.js"):
            self.assertEqual(self.client.get(asset).status_code, 200, asset)


if __name__ == "__main__":
    unittest.main()
