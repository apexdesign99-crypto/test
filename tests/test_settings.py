"""データソース設定（API キーの保存・マスク・接続テスト）のテスト。"""

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webapp import settings_store

HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx")

if HAS_FASTAPI:
    from fastapi.testclient import TestClient

    from webapp.main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "abcd1234efgh5678ijkl"

#: 環境変数の影響を受けないようにするための空設定
CLEAN_ENV = {
    "REINFOLIB_API_KEY": "",
    "AI_LAND_DESIGN_LIVE": "",
    "AI_LAND_DESIGN_ZONING_GEOJSON": "",
    "AI_LAND_DESIGN_GEOCODE_TABLE": "",
    "AI_LAND_DESIGN_GEOCODE_CACHE": "",
}


class MaskTest(unittest.TestCase):
    def test_keeps_only_the_last_four_characters(self):
        self.assertEqual(settings_store.mask(SECRET), "*" * 16 + "ijkl")

    def test_short_secret_is_fully_masked(self):
        self.assertEqual(settings_store.mask("abc"), "***")

    def test_empty(self):
        self.assertEqual(settings_store.mask(""), "")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        patcher = mock.patch.dict(
            os.environ, {**CLEAN_ENV, settings_store.ENV_CONFIG_PATH: str(self.path)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_empty_by_default(self):
        settings, origins = settings_store.load()
        self.assertEqual(settings.reinfolib_api_key, "")
        self.assertEqual(origins["reinfolib_api_key"], "未設定")

    def test_save_and_load(self):
        settings_store.update({"reinfolib_api_key": SECRET, "live": True})
        settings, origins = settings_store.load()
        self.assertEqual(settings.reinfolib_api_key, SECRET)
        self.assertTrue(settings.live)
        self.assertEqual(origins["reinfolib_api_key"], "設定ファイル")

    def test_file_permissions_are_owner_only(self):
        _, path = settings_store.update({"reinfolib_api_key": SECRET})
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_blank_key_keeps_the_existing_one(self):
        settings_store.update({"reinfolib_api_key": SECRET})
        settings, _ = settings_store.update({"reinfolib_api_key": "", "live": True})
        self.assertEqual(settings.reinfolib_api_key, SECRET)

    def test_clear_api_key(self):
        settings_store.update({"reinfolib_api_key": SECRET, "live": True})
        settings = settings_store.clear_api_key()
        self.assertEqual(settings.reinfolib_api_key, "")
        self.assertTrue(settings.live)  # 他の設定は残る

    def test_file_wins_over_environment(self):
        with mock.patch.dict(os.environ, {"REINFOLIB_API_KEY": "from-env"}):
            settings_store.update({"reinfolib_api_key": SECRET})
            settings, origins = settings_store.load()
            self.assertEqual(settings.reinfolib_api_key, SECRET)
            self.assertEqual(origins["reinfolib_api_key"], "設定ファイル")

    def test_environment_is_used_when_no_file(self):
        with mock.patch.dict(os.environ, {"REINFOLIB_API_KEY": "from-env", "AI_LAND_DESIGN_LIVE": "1"}):
            settings, origins = settings_store.load()
            self.assertEqual(settings.reinfolib_api_key, "from-env")
            self.assertTrue(settings.live)
            self.assertEqual(origins["reinfolib_api_key"], "環境変数")

    def test_broken_file_does_not_crash(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ broken", encoding="utf-8")
        settings, _ = settings_store.load()
        self.assertEqual(settings.reinfolib_api_key, "")

    def test_public_view_never_contains_the_secret(self):
        settings_store.update({"reinfolib_api_key": SECRET})
        settings, origins = settings_store.load()
        view = json.dumps(settings.public_view(origins), ensure_ascii=False)
        self.assertNotIn(SECRET, view)
        self.assertIn("ijkl", view)  # 末尾4文字だけ確認できる


@unittest.skipUnless(HAS_FASTAPI, "fastapi / httpx が未インストール")
class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        patcher = mock.patch.dict(
            os.environ, {**CLEAN_ENV, settings_store.ENV_CONFIG_PATH: str(self.path)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(app)

    def test_initial_state_is_not_ready(self):
        data = self.client.get("/api/settings").json()
        self.assertFalse(data["reinfolib_api_key_set"])
        self.assertFalse(data["ready"])
        self.assertIn("reason", data)
        self.assertIn(str(self.path), data["config_path"])

    def test_save_key_and_enable_live(self):
        response = self.client.put(
            "/api/settings", json={"reinfolib_api_key": SECRET, "live": True}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["reinfolib_api_key_set"])
        self.assertTrue(data["ready"])
        self.assertTrue(any("不動産情報ライブラリ" in s for s in data["sources"]))

    def test_response_never_leaks_the_key(self):
        self.client.put("/api/settings", json={"reinfolib_api_key": SECRET, "live": True})
        for response in (
            self.client.get("/api/settings"),
            self.client.put("/api/settings", json={"live": True}),
        ):
            self.assertNotIn(SECRET, response.text)
            self.assertIn("ijkl", response.text)

    def test_blank_key_keeps_the_saved_one(self):
        self.client.put("/api/settings", json={"reinfolib_api_key": SECRET})
        data = self.client.put("/api/settings", json={"live": True}).json()
        self.assertTrue(data["reinfolib_api_key_set"])
        self.assertEqual(settings_store.load()[0].reinfolib_api_key, SECRET)

    def test_delete_key(self):
        self.client.put("/api/settings", json={"reinfolib_api_key": SECRET})
        data = self.client.delete("/api/settings/api-key").json()
        self.assertFalse(data["reinfolib_api_key_set"])

    def test_missing_file_is_rejected(self):
        response = self.client.put("/api/settings", json={"zoning_geojson": "/no/such/file.json"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("見つかりません", response.json()["detail"])

    def test_existing_file_is_accepted(self):
        response = self.client.put(
            "/api/settings", json={"zoning_geojson": str(FIXTURES / "zoning_a29.json")}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["zoning_geojson"].endswith("zoning_a29.json"))

    def test_api_name_is_validated(self):
        self.assertEqual(
            self.client.put("/api/settings", json={"zoning_api": "XKT013"}).status_code, 200
        )
        self.assertEqual(
            self.client.put("/api/settings", json={"zoning_api": "../etc/passwd"}).status_code, 422
        )

    def test_settings_drive_the_resolver(self):
        self.client.put(
            "/api/settings",
            json={
                "geocode_table": str(FIXTURES / "geocode.json"),
                "zoning_geojson": str(FIXTURES / "zoning_a29.json"),
                "live": False,
            },
        )
        response = self.client.post(
            "/api/resolve", json={"address": "東京都世田谷区代田1-1-1", "area_m2": 180}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["zoning"]["use_district"], "第一種住居地域")

    def test_connection_test_with_local_sources(self):
        self.client.put(
            "/api/settings",
            json={
                "geocode_table": str(FIXTURES / "geocode.json"),
                "zoning_geojson": str(FIXTURES / "zoning_a29.json"),
                "live": False,
            },
        )
        data = self.client.post("/api/settings/test").json()
        names = {r["name"]: r for r in data["results"]}
        self.assertTrue(any("ローカル辞書" in name for name in names))
        zoning = next(r for name, r in names.items() if "A29" in name)
        self.assertTrue(zoning["ok"])
        self.assertIn("2 件", zoning["detail"])

    def test_connection_test_skips_when_live_is_off(self):
        self.client.put("/api/settings", json={"reinfolib_api_key": SECRET, "live": False})
        data = self.client.post("/api/settings/test").json()
        zoning = next(r for r in data["results"] if "不動産情報ライブラリ" in r["name"])
        self.assertTrue(zoning["skipped"])
        self.assertIn("外部 API を利用", zoning["detail"])

    def test_settings_page_is_served(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn("データソース設定", response.text)
        self.assertIn("認証機能はありません", response.text)


if __name__ == "__main__":
    unittest.main()
