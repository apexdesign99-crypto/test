import pytest

from ai_employee.profile import DEFAULT_TOOLS, EmployeeProfile, build_profile, slugify


def test_職種テンプレートから採用できる():
    p = build_profile("sato", "佐藤 AI", "sales")
    assert p.role == "営業アシスタント"
    assert p.department == "営業部"
    assert p.responsibilities
    assert p.tools == DEFAULT_TOOLS


def test_上書き指定がテンプレートより優先される():
    p = build_profile("sato", "佐藤 AI", "sales", role="営業部長", web_access=True)
    assert p.role == "営業部長"
    assert p.web_access is True


def test_None_の上書きは無視される():
    p = build_profile("sato", "佐藤 AI", "sales", role=None)
    assert p.role == "営業アシスタント"


def test_リサーチャーは既定で_web_権限を持つ():
    assert build_profile("r", "調査 AI", "researcher").web_access is True


def test_未知の職種は拒否される():
    with pytest.raises(ValueError, match="未知の職種"):
        build_profile("x", "X", "ninja")


def test_保存と読み込みで内容が一致する(tmp_path):
    p = build_profile("sato", "佐藤 AI", "support")
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
    prompt = build_profile("sato", "佐藤 AI", "sales").system_prompt()
    assert "佐藤 AI" in prompt
    assert "営業アシスタント" in prompt
    assert "商談メモの記録" in prompt
    assert "record_note" in prompt  # 勤務ルール


def test_system_prompt_に揮発情報を含めない():
    """時刻などを混ぜるとプロンプトキャッシュが毎回無効化されるため。"""
    prompt = build_profile("sato", "佐藤 AI").system_prompt()
    assert build_profile("sato", "佐藤 AI").system_prompt() == prompt
    assert "現在時刻" not in prompt


@pytest.mark.parametrize(
    "raw,expected",
    [("Sato AI", "sato-ai"), ("  A_b  ", "a_b"), ("営業", "employee")],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected
