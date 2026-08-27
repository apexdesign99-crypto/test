"""土地診断のテスト。

診断は法適合の判断ではないので、
「計算していないことを計算したと言わない」ことを重点的に確認する。
"""

import pytest

from ai_employee.company import CompanyError, OfficeProfile, ProjectLedger
from ai_employee.land import (
    REQUIRED_CONFIRMATIONS,
    LandConditions,
    LandError,
    LandSettings,
    diagnose,
)


def conditions(**overrides) -> LandConditions:
    base = {
        "site_area": 150.0,
        "zoning": "第一種低層住居専用地域",
        "building_coverage": 50.0,
        "floor_area_ratio": 100.0,
    }
    base.update(overrides)
    return LandConditions(**base)


@pytest.fixture
def settings() -> LandSettings:
    return LandSettings()


# ---------------------------------------------------------------- 建蔽率


def test_建築面積は敷地面積と建蔽率の積(settings):
    result = diagnose(conditions(site_area=150, building_coverage=50), settings)
    assert result["building_area_max"] == 75.0
    assert "150.0㎡ × 建蔽率 50.0%" in result["coverage_basis"]


def test_緩和は既定では適用されない(settings):
    """適用可否は行政判断なので、確認できたものだけを入力させる。"""
    result = diagnose(conditions(), settings)
    assert result["coverage_relaxations"] == []
    assert "緩和は適用していない" in result["coverage_basis"]


def test_確認できた緩和は加算され要確認と明記される(settings):
    result = diagnose(conditions(relaxations=["corner_lot"]), settings)
    assert result["building_coverage_applied"] == 60.0
    assert result["building_area_max"] == 90.0
    assert "行政に要確認" in result["coverage_basis"]


def test_緩和を重ねても100を超えない(settings):
    result = diagnose(
        conditions(building_coverage=90, relaxations=["corner_lot", "fireproof"]), settings
    )
    assert result["building_coverage_applied"] == 100


def test_未知の緩和は拒否される(settings):
    with pytest.raises(LandError, match="未知の緩和"):
        diagnose(conditions(relaxations=["special_deal"]), settings)


# ---------------------------------------------------------------- 容積率


def test_道路幅員による制限が効かない場合は指定容積率(settings):
    result = diagnose(conditions(floor_area_ratio=100, road_width=6.0), settings)
    assert result["floor_area_ratio_applied"] == 100.0
    assert result["floor_area_ratio_road_limit"] == 240.0  # 6.0 × 4/10


def test_道路幅員による制限が効く場合は小さい方(settings):
    result = diagnose(
        conditions(zoning="商業地域", floor_area_ratio=400, road_width=4.5), settings
    )
    assert result["floor_area_ratio_road_limit"] == 270.0  # 4.5 × 6/10
    assert result["floor_area_ratio_applied"] == 270.0
    assert result["total_floor_area_max"] == 405.0  # 150 × 270%


def test_住居系と非住居系で係数が変わる(settings):
    residential = diagnose(conditions(road_width=5.0, floor_area_ratio=999), settings)
    commercial = diagnose(
        conditions(zoning="商業地域", road_width=5.0, floor_area_ratio=999), settings
    )
    assert residential["floor_area_ratio_road_limit"] == 200.0  # 5.0 × 4/10
    assert commercial["floor_area_ratio_road_limit"] == 300.0   # 5.0 × 6/10


def test_係数は事務所設定であると根拠に明記される(settings):
    result = diagnose(conditions(road_width=4.0, floor_area_ratio=999), settings)
    assert "事務所プロフィールの設定値" in result["floor_area_basis"]


def test_設定を変えれば係数も変わる():
    custom = LandSettings(road_coefficient_residential=6)
    result = diagnose(conditions(road_width=5.0, floor_area_ratio=999), custom)
    assert result["floor_area_ratio_road_limit"] == 300.0


def test_道路幅員が未入力なら制限を計算していないと明記(settings):
    result = diagnose(conditions(), settings)
    assert result["floor_area_ratio_road_limit"] is None
    assert "計算していない" in result["floor_area_basis"]
    assert any("前面道路幅員" in m for m in result["missing_inputs"])


def test_広い道路では幅員による制限を見ない(settings):
    result = diagnose(conditions(road_width=12.0, floor_area_ratio=200), settings)
    assert result["floor_area_ratio_road_limit"] is None
    assert result["floor_area_ratio_applied"] == 200.0


# ---------------------------------------------------------------- 接道義務


def test_接道義務を判定できる(settings):
    result = diagnose(conditions(road_width=4.0, road_contact=2.5), settings)
    assert result["road_check"]["passes"] is True


@pytest.mark.parametrize(
    "width,contact,width_ok,contact_ok",
    [(3.0, 5.0, False, True), (5.0, 1.5, True, False), (3.0, 1.0, False, False)],
)
def test_基準を下回れば満たさないと判定する(settings, width, contact, width_ok, contact_ok):
    check = diagnose(conditions(road_width=width, road_contact=contact), settings)["road_check"]
    assert check["width_ok"] is width_ok
    assert check["contact_ok"] is contact_ok
    assert check["passes"] is False


def test_情報が足りなければ判定しない(settings):
    """判定できないときに「満たしている」と言わせない。"""
    check = diagnose(conditions(road_width=4.0), settings)["road_check"]
    assert check == {"judged": False}


# ------------------------------------------------------- 計算していないこと


def test_確認事項が必ず全件返る(settings):
    result = diagnose(conditions(), settings)
    assert len(result["required_confirmations"]) == len(REQUIRED_CONFIRMATIONS)
    items = [c["item"] for c in result["required_confirmations"]]
    for expected in ("斜線制限", "日影規制", "地区計画", "がけ・擁壁", "ハザード"):
        assert expected in items


def test_但し書きに計算していない項目が明記される(settings):
    disclaimer = diagnose(conditions(), settings)["disclaimer"]
    assert "法適合の判断ではない" in disclaimer
    assert "斜線制限" in disclaimer
    assert "所管行政庁" in disclaimer


# ---------------------------------------------------------------- 入力検証


def test_必須項目の不足はまとめて示される():
    with pytest.raises(LandError, match="用途地域、指定建蔽率") as exc:
        LandConditions(
            site_area=100, zoning=None, building_coverage=None, floor_area_ratio=None
        ).validate()
    assert "推測しないこと" in str(exc.value)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"site_area": 0}, "敷地面積"),
        ({"zoning": "住宅地"}, "不正な用途地域"),
        ({"building_coverage": 0}, "建蔽率"),
        ({"building_coverage": 120}, "建蔽率"),
        ({"floor_area_ratio": 0}, "容積率"),
        ({"road_width": -1}, "前面道路幅員"),
        ({"road_contact": -1}, "接道長さ"),
    ],
)
def test_不正な入力は拒否される(settings, overrides, message):
    with pytest.raises(LandError, match=message):
        diagnose(conditions(**overrides), settings)


def test_設定の未知項目は拒否される():
    with pytest.raises(LandError, match="未知の項目"):
        LandSettings.from_dict({"road_coefficient_residential": 4, "magic": 1})


def test_設定が空なら既定値が使われる():
    assert LandSettings.from_dict(None) == LandSettings()
    assert LandSettings.from_dict({}) == LandSettings()


# ------------------------------------------------------------ 案件との連携


@pytest.fixture
def ledger(tmp_path) -> ProjectLedger:
    return ProjectLedger(tmp_path)


@pytest.fixture
def office(tmp_path) -> OfficeProfile:
    profile = OfficeProfile(name="A設計")
    profile.save(tmp_path)
    return profile


def test_敷地条件が未記録なら診断できない(ledger, office):
    """調べていないのに診断結果を出させない。"""
    project = ledger.add("佐々木様 新築")
    with pytest.raises(CompanyError, match="敷地条件が未記録"):
        ledger.diagnose_land(project["id"], office)


def test_記録した条件で診断できる(ledger, office):
    project = ledger.add("佐々木様 新築")
    ledger.record_land(
        project["id"],
        conditions(site_area=132.5, road_width=4.0, road_contact=6.2),
        by="eigyo",
    )
    result = ledger.diagnose_land(project["id"], office)
    assert result["building_area_max"] == 66.25
    assert result["recorded_by"] == "eigyo"
    assert result["project_name"] == "佐々木様 新築"


def test_敷地条件の記録は履歴に残る(ledger, office):
    project = ledger.add("佐々木様 新築")
    ledger.record_land(project["id"], conditions(), by="eigyo")
    entry = ledger.get(project["id"])["history"][-1]
    assert entry["by"] == "eigyo"
    assert "敷地条件を記録" in entry["entry"]
    assert "第一種低層住居専用地域" in entry["entry"]


def test_不正な条件は記録されない(ledger):
    project = ledger.add("佐々木様 新築")
    with pytest.raises(LandError):
        ledger.record_land(project["id"], conditions(site_area=-1))
    assert ledger.get(project["id"])["land"] is None


def test_事務所設定が診断に反映される(ledger, tmp_path):
    office = OfficeProfile(name="A設計", land_settings={"road_coefficient_residential": 6})
    office.save(tmp_path)
    project = ledger.add("佐々木様 新築")
    ledger.record_land(project["id"], conditions(road_width=5.0, floor_area_ratio=999))
    assert ledger.diagnose_land(project["id"], office)["floor_area_ratio_road_limit"] == 300.0
