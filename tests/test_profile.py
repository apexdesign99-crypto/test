import pytest

from ai_employee.profile import DEFAULT_TOOLS, EmployeeProfile, build_profile, slugify


def test_職種テンプレートから採用できる():
    p = build_profile("eigyo", "営業 AI", "sales")
    assert p.role == "営業担当"
    assert p.department == "営業部"
    assert p.responsibilities
    assert p.tools == DEFAULT_TOOLS


def test_上書き指定がテンプレートより優先される():
    p = build_profile("eigyo", "営業 AI", "sales", role="営業部長", web_access=True)
    assert p.role == "営業部長"
    assert p.web_access is True


def test_None_の上書きは無視される():
    p = build_profile("eigyo", "営業 AI", "sales", role=None)
    assert p.role == "営業担当"


def test_マーケ担当は既定で_web_権限を持つ():
    assert build_profile("marke", "マーケ AI", "marketing").web_access is True


def test_設計以外の職種は_web_権限を持たない():
    assert build_profile("bim", "BIM AI", "bim").web_access is False


def test_未知の職種は拒否される():
    with pytest.raises(ValueError, match="未知の職種"):
        build_profile("x", "X", "ninja")


def test_全テンプレートが採用可能で職務が定義されている():
    from ai_employee.profile import TEMPLATES

    for key in TEMPLATES:
        p = build_profile("x", "X", key)
        assert p.responsibilities, key
        assert p.guidelines, key
        assert p.mission.strip(), key


def test_保存と読み込みで内容が一致する(tmp_path):
    p = build_profile("jimu", "事務 AI", "office")
    path = tmp_path / "profile.json"
    p.save(path)
    assert EmployeeProfile.load(path) == p


def test_未知の項目を含む職務定義書は拒否される():
    with pytest.raises(ValueError, match="未知の項目"):
        EmployeeProfile.from_dict({"employee_id": "a", "name": "A", "salary": 100})


def test_必須項目が欠けていれば拒否される():
    with pytest.raises(ValueError, match="employee_id"):
        EmployeeProfile.from_dict({"name": "A"})


def test_system_prompt_に職務と行動指針が含まれる():
    prompt = build_profile("eigyo", "営業 AI", "sales").system_prompt()
    assert "営業 AI" in prompt
    assert "営業担当" in prompt
    assert "プラン提案・概算見積の下書き作成" in prompt
    assert "record_note" in prompt  # 勤務ルール
    assert "list_projects" in prompt  # 案件台帳の運用ルール


def test_BIM担当は法規を断定しないよう指示される():
    prompt = build_profile("bim", "BIM AI", "bim").system_prompt()
    assert "所管行政庁" in prompt
    assert "記憶で断定せず" in prompt
    # モデルを直接操作できないことを自認させる
    assert "Revit" in prompt


def test_マーケ担当は個人情報と優良誤認を戒められる():
    prompt = build_profile("marke", "マーケ AI", "marketing").system_prompt()
    assert "掲載許諾" in prompt
    assert "優良誤認" in prompt


def test_system_prompt_に揮発情報を含めない():
    """時刻などを混ぜるとプロンプトキャッシュが毎回無効化されるため。"""
    prompt = build_profile("eigyo", "営業 AI").system_prompt()
    assert build_profile("eigyo", "営業 AI").system_prompt() == prompt
    assert "現在時刻" not in prompt


@pytest.mark.parametrize(
    "raw,expected",
    [("Sato AI", "sato-ai"), ("  A_b  ", "a_b"), ("営業", "employee")],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected
