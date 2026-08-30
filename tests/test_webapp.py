"""ブラウザ画面のテスト。

閲覧専用であること、外部から開けないこと、
施主名などが HTML に正しくエスケープされることを重点的に確認する。
"""

import json
import re
import threading
import urllib.request
from datetime import timedelta

import pytest

from ai_employee.billing import EXAMPLE_SCHEDULE
from ai_employee.company import OfficeProfile, ProjectLedger
from ai_employee.competitor import CompetitorLedger
from ai_employee.land import LandConditions
from ai_employee.profile import build_profile
from ai_employee.webapp import Views, serve
from ai_employee.workspace import Workspace, now


@pytest.fixture
def office_root(tmp_path):
    """一通りのデータが入った事務所を用意する。"""
    OfficeProfile(
        name="アペックス設計事務所",
        areas=["愛知県一宮市", "岐阜県岐阜市"],
        specialties=["デザイン性", "自然素材"],
        unit_prices={"戸建住宅": [75, 95]},
        design_fee_rate=10,
        billing_schedule=[
            {"label": l, "ratio": r, "stage": s} for l, r, s in EXAMPLE_SCHEDULE
        ],
        tax_rate=10,
    ).save(tmp_path)

    for eid, name, tpl in [("shukyaku", "集客 AI", "lead"), ("eigyo", "営業 AI", "sales")]:
        ws = Workspace(eid, tmp_path)
        ws.save_profile(build_profile(eid, name, tpl))

    ledger = ProjectLedger(tmp_path)
    a = ledger.add("K様邸 新築", "K様", "戸建住宅", source="Instagram",
                   site="愛知県一宮市", owner="eigyo", by="shukyaku")
    ledger.update(a["id"], "実施設計に着手", by="eigyo", stage="実施設計")
    ledger.setup_billing(a["id"], 3_780_000, OfficeProfile.load(tmp_path), by="jimu")
    ledger.record_hearing(a["id"], {"budget": "総額4200万円"}, by="eigyo")
    ledger.record_land(
        a["id"],
        LandConditions(site_area=171.9, zoning="第一種低層住居専用地域",
                       building_coverage=50, floor_area_ratio=100,
                       road_width=4.0, road_contact=7.5),
        by="eigyo",
    )
    ledger.record_consent(a["id"], "条件付き", "施主名は伏せる", by="marke")

    stale = ledger.add("T様邸 平屋", "T様", "戸建住宅", source="Instagram", owner="shukyaku")
    data = json.loads(ledger.path.read_text(encoding="utf-8"))
    for record in data:
        if record["id"] == stale["id"]:
            record["updated_at"] = (now() - timedelta(days=30)).isoformat(timespec="seconds")
    ledger.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    CompetitorLedger(tmp_path).record(
        "あかつき工務店", "愛知県一宮市", ["https://example.com/a"], "工務店",
        ["価格の安さ"], instagram="@akatsuki", followers=4200, by="shukyaku")
    return tmp_path


@pytest.fixture
def views(office_root) -> Views:
    return Views(office_root)


# ---------------------------------------------------------------- 各画面


def test_ダッシュボードに要対応が出る(views):
    html = views.dashboard()
    assert "請求の要対応" in html
    assert "追客が止まっている案件" in html
    assert "T様邸 平屋" in html
    assert "パイプライン" in html


def test_反響段階はヒアリング未確認として出さない(views, office_root):
    """まだ面談していない案件を「提案前に確認が必要」に出すとノイズになる。"""
    html = views.dashboard()
    section = html[html.find("提案前に確認が必要な案件"):] if "提案前に確認が必要な案件" in html else ""
    assert "K様邸 新築" in section       # 実施設計まで進んでいるので出る
    assert "T様邸 平屋" not in section   # 反響のままなので出さない


def test_案件一覧に全案件が出る(views):
    html = views.projects()
    assert "K様邸 新築" in html and "T様邸 平屋" in html


def test_案件詳細に各セクションが揃う(views, office_root):
    project = ProjectLedger(office_root).list(query="K様邸")[0]
    html = views.project(project["id"])
    for heading in ("ヒアリング", "土地診断", "請求", "掲載許諾", "経緯"):
        assert heading in html, heading
    assert "総額4200万円" in html
    assert "85.95" in html               # 建築面積の上限
    assert "3,780,000" in html or "1,134,000" in html


def test_土地診断には但し書きが必ず付く(views, office_root):
    project = ProjectLedger(office_root).list(query="K様邸")[0]
    html = views.project(project["id"])
    assert "法適合の判断ではない" in html
    assert "判定していない項目" in html


def test_請求画面に合計と要対応が出る(views):
    html = views.billing()
    assert "契約額の合計" in html
    assert "要対応" in html
    assert "税別" in html


def test_競合画面に出典リンクが出る(views):
    html = views.competitors_view()
    assert "あかつき工務店" in html
    assert "https://example.com/a" in html   # 出典を必ず示す
    assert "判断材料であって結論ではない" in html


def test_社員画面に権限と充足状況が出る(views):
    html = views.roster_view()
    assert "集客 AI" in html
    assert "Web検索あり" in html            # 集客担当の権限
    assert "事務所プロフィールの充足" in html


def test_設定が空でも画面が落ちない(tmp_path):
    """採用前・データ投入前でもエラーにしない。"""
    views = Views(tmp_path)
    for render in (views.dashboard, views.projects, views.billing,
                   views.competitors_view, views.roster_view):
        assert render()


# ------------------------------------------------------------ エスケープ


def test_施主名などがエスケープされる(tmp_path):
    """台帳の文字列がそのまま HTML として解釈されないこと。"""
    OfficeProfile(name="<b>事務所</b>").save(tmp_path)
    ledger = ProjectLedger(tmp_path)
    ledger.add("<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "戸建住宅")

    html = Views(tmp_path).projects()
    # 実行されうるタグの形になっていないこと(文字としての出現は無害)
    assert "<script" not in html.replace("<script>", "")  # ページ自身の script も無い
    assert "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_引用符もエスケープされる(tmp_path):
    """属性の中に流れ込んでも壊れないこと。"""
    OfficeProfile(name='A"設計').save(tmp_path)
    ProjectLedger(tmp_path).add('K様"邸', "K様", "戸建住宅")
    html = Views(tmp_path).projects()
    assert "&quot;" in html


# ---------------------------------------------------------------- 配信


@pytest.fixture
def server(office_root):
    httpd = serve(office_root, port=0)   # 空きポートを OS に選ばせる
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def get(httpd, path: str):
    host, port = httpd.server_address[:2]
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_外部からは開けない(server):
    """施主の個人情報を扱うので 127.0.0.1 のみで待ち受ける。"""
    assert server.server_address[0] == "127.0.0.1"


@pytest.mark.parametrize(
    "path", ["/", "/projects", "/billing", "/competitors", "/roster"]
)
def test_各ページが表示できる(server, path):
    status, body = get(server, path)
    assert status == 200
    assert "アペックス設計事務所" in body


def test_案件詳細が表示できる(server, office_root):
    project = ProjectLedger(office_root).list(query="K様邸")[0]
    status, body = get(server, f"/projects/{project['id']}")
    assert status == 200
    assert "K様邸 新築" in body


def test_存在しないページは404(server):
    status, body = get(server, "/nope")
    assert status == 404
    assert "見つかりません" in body


def test_存在しない案件はエラー画面になり落ちない(server):
    status, body = get(server, "/projects/deadbeef")
    assert status == 500
    assert "表示できませんでした" in body
    assert "案件が見つかりません" in body


def test_書き込みは受け付けない(server):
    """ブラウザ操作で台帳を壊さないため、GET しか実装していない。"""
    host, port = server.server_address[:2]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/projects", data=b"x=1", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 501


def test_埋め込み防止のヘッダが付く(server):
    host, port = server.server_address[:2]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"


def test_外部の読み込み先を持たない(server):
    """社内ネットワークが外に出られなくても、また通信が漏れないように。"""
    _, body = get(server, "/")
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?://', body)


def test_発信画面に計画と注意が出る(office_root):
    from ai_employee.instagram_plan import InstagramPlan

    plan = InstagramPlan(office_root)
    posts = plan.draft_month("2026-09", "standard")
    plan.update(posts[0]["id"], title="光の回る家", assets_ready=True, status="投稿済")
    plan.update(posts[1]["id"], status="素材待ち")

    html = Views(office_root).plan_view("2026-09")
    assert "2026-09 の投稿計画" in html
    assert "光の回る家" in html
    assert "素材未確認" in html
    assert "題材が未定" in html
    # 未接続なら実績の代わりに接続手順を案内する
    assert "Instagram に接続していません" in html


def test_計画がない月でも落ちない(office_root):
    html = Views(office_root).plan_view("2030-01")
    assert "計画がありません" in html


def test_実績が発信画面に出る(office_root):
    from ai_employee.instagram_api import connect, sync

    def fake(url):
        if "/me?" in url:
            return {"id": "1", "username": "apex_sekkei", "account_type": "BUSINESS",
                    "media_count": 42, "followers_count": 1280}
        if "me/media" in url:
            return {"data": [{"id": "m1", "caption": "北向きの敷地に\n光が回る家",
                              "media_type": "CAROUSEL_ALBUM",
                              "permalink": "https://instagram.com/p/x1",
                              "timestamp": "2026-09-03T10:00:00+0000",
                              "like_count": 84, "comments_count": 6}]}
        if "insights" in url:
            return {"data": [{"name": "views", "values": [{"value": 3120}]}]}
        return {}

    connect("SECRET", office_root, fake)
    sync(office_root, 25, fake)

    html = Views(office_root).plan_view("2026-09")
    assert "フォロワー" in html and "1,280" not in html   # 生値をそのまま出す
    assert "3,120" in html
    assert "北向きの敷地に" in html
    assert "SECRET" not in html                          # トークンは出さない
    assert "0 ではありません" in html


def test_期限切れの警告が画面に出る(office_root):
    from datetime import timedelta

    from ai_employee.instagram_api import Credentials, save_credentials
    from ai_employee.workspace import now

    save_credentials(Credentials(
        access_token="T", username="apex",
        expires_at=(now() - timedelta(days=1)).isoformat(timespec="seconds"),
    ), office_root)
    assert "有効期限が切れています" in Views(office_root).plan_view("2026-09")


def test_点検画面に指摘と免責が出る(office_root):
    import json as _json

    from ai_employee.company import ProjectLedger

    ledger = ProjectLedger(office_root)
    project = ledger.list(query="K様邸")[0]
    data = _json.loads(ledger.path.read_text(encoding="utf-8"))
    for record in data:
        if record["id"] == project["id"]:
            record["consent"] = {"status": "未確認", "conditions": ""}
            record["publications"] = [{"at": "2026-08-01T10:00:00",
                                       "channel": "Instagram", "title": "完成",
                                       "url": "", "by": "marke"}]
    ledger.path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")

    html = Views(office_root).audit_view()
    assert "社内点検" in html
    assert "掲載許諾" in html
    assert "安全性の保証ではない" in html
    assert "点検した項目" in html


def test_指摘がなくても安全だとは書かない(tmp_path):
    from ai_employee.company import OfficeProfile

    OfficeProfile(name="A設計").save(tmp_path)
    html = Views(tmp_path).audit_view()
    assert "今回の点検では指摘はありませんでした" in html
    assert "安全性の保証ではない" in html
