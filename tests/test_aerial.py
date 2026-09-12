"""航空写真への重ね合わせのテスト。

縮尺と方位が合っていない合成画像は、絵として嘘になる。
「なんとなく合っている」を通さないことを確認する。
"""

import math

import pytest

from ai_employee.aerial import (
    ATTRIBUTION_BAND,
    EARTH_CIRCUMFERENCE,
    ENV_API_KEY,
    AerialError,
    MapSpec,
    Overlay,
    _redacted,
    api_key,
    compose,
    fetch_map,
    ground_distance,
    map_url,
    metres_per_pixel,
    pixel_offset,
    plan_overlay,
    render,
)

Image = pytest.importorskip("PIL.Image")

NAGOYA = (35.1815, 136.9066)


def png(size=(1280, 1280), colour=(60, 90, 60, 255), band=None):
    """PNG のバイト列を作る。既定は scale=2・640 角のキャンバス。

    band を渡すと下端だけ別色にする(帰属表示の帯を見分けるため)。
    """
    import io

    image = Image.new("RGBA", size, colour)
    if band is not None:
        strip = Image.new("RGBA", (size[0], band[0]), band[1])
        image.paste(strip, (0, size[1] - band[0]))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def 建物画像(tmp_path):
    """横長(間口 > 奥行)の不透明な板。回転が効いたか測れる。"""
    path = tmp_path / "building.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 255)).save(path)
    return path


# ---------------------------------------------------------------- 縮尺


def test_緯度で縮尺が変わる():
    """Web メルカトルは高緯度ほど引き伸ばされる。cos を忘れると建物が大きくなる。"""
    赤道 = metres_per_pixel(0.0, 20)
    愛知 = metres_per_pixel(35.18, 20)
    assert 愛知 == pytest.approx(赤道 * math.cos(math.radians(35.18)))
    # 補正を忘れると 22% 大きく描かれる。
    assert 赤道 / 愛知 == pytest.approx(1.22, abs=0.01)


def test_縮尺はズームで倍々になる():
    assert metres_per_pixel(35.0, 19) == pytest.approx(metres_per_pixel(35.0, 20) * 2)


def test_解像度倍率を上げても実寸は変わらない():
    """scale=2 は画素が細かくなるだけ。1 画素あたりの地上距離は半分になる。"""
    assert metres_per_pixel(35.0, 20, scale=2) == pytest.approx(
        metres_per_pixel(35.0, 20) / 2)


def test_ズーム0の世界一周は256画素():
    assert metres_per_pixel(0.0, 0) * 256 == pytest.approx(EARTH_CIRCUMFERENCE)


# ---------------------------------------------------------------- 画素のずれ


def test_北は上_東は右():
    北 = pixel_offset(NAGOYA, (NAGOYA[0] + 0.001, NAGOYA[1]), 20)
    東 = pixel_offset(NAGOYA, (NAGOYA[0], NAGOYA[1] + 0.001), 20)
    assert 北[1] < 0 and 北[0] == pytest.approx(0)
    assert 東[0] > 0 and 東[1] == pytest.approx(0)


def test_ずれは実距離と縮尺に一致する():
    """メルカトルの座標差から出したずれが、m ÷ (m/画素) と合うこと。"""
    point = (NAGOYA[0], NAGOYA[1] + 0.0005)
    dx, _ = pixel_offset(NAGOYA, point, 20, scale=2)
    expected = ground_distance(NAGOYA, point) / metres_per_pixel(NAGOYA[0], 20, scale=2)
    assert dx == pytest.approx(expected, rel=0.001)


def test_同じ点ならずれはゼロ():
    assert pixel_offset(NAGOYA, NAGOYA, 21, 2) == (0.0, 0.0)


# ---------------------------------------------------------------- 入力の検査


def test_緯度経度の取り違えを弾く():
    # 経度 136 を緯度に入れた、よくある間違い。
    with pytest.raises(AerialError, match="緯度と経度が逆"):
        MapSpec(latitude=136.9, longitude=35.1)


def test_ズームと画像サイズの上限を守る():
    with pytest.raises(AerialError, match="ズーム"):
        MapSpec(latitude=35.1, longitude=136.9, zoom=25)
    with pytest.raises(AerialError, match="640"):
        MapSpec(latitude=35.1, longitude=136.9, width=1200)
    with pytest.raises(AerialError, match="scale"):
        MapSpec(latitude=35.1, longitude=136.9, scale=4)


def test_実寸が正でなければ拒否する(建物画像):
    with pytest.raises(AerialError, match="間口"):
        Overlay(image=建物画像, latitude=35.1, longitude=136.9,
                width_m=0, depth_m=6.37)


def test_画像がなければ拒否する(tmp_path):
    with pytest.raises(AerialError, match="見つかりません"):
        Overlay(image=tmp_path / "ない.png", latitude=35.1, longitude=136.9,
                width_m=10.9, depth_m=6.4)


def test_方位角は0から360に丸める(建物画像):
    overlay = Overlay(image=建物画像, latitude=35.1, longitude=136.9,
                      width_m=10.9, depth_m=6.4, bearing=-45)
    assert overlay.bearing == 315


# ---------------------------------------------------------------- 配置の検算


def spec(**kwargs):
    base = dict(latitude=NAGOYA[0], longitude=NAGOYA[1], zoom=20, scale=2)
    return MapSpec(**{**base, **kwargs})


def overlay(画像, **kwargs):
    base = dict(image=画像, latitude=NAGOYA[0], longitude=NAGOYA[1],
                width_m=10.92, depth_m=6.37)
    return Overlay(**{**base, **kwargs})


def test_実寸から画素サイズを出す(建物画像):
    geometry = plan_overlay(spec(), overlay(建物画像))
    mpp = metres_per_pixel(NAGOYA[0], 20, 2)
    assert geometry["size_px"][0] == pytest.approx(10.92 / mpp)
    assert geometry["size_px"][1] == pytest.approx(6.37 / mpp)


def test_小さすぎて読めない縮尺は拒否する(建物画像):
    """API を叩いてから「読めない」と分かるのは無駄。取得前に止める。"""
    with pytest.raises(AerialError, match="zoom を上げる"):
        plan_overlay(spec(zoom=12), overlay(建物画像))


def test_写真の外に出る指定は拒否する(建物画像):
    with pytest.raises(AerialError, match="写真の外"):
        plan_overlay(spec(), overlay(建物画像, latitude=NAGOYA[0] + 0.01))


def test_中心から遠い建物は警告する(建物画像):
    """緯度経度の取り違えは、写真に収まってしまうと気付けない。"""
    geometry = plan_overlay(
        spec(zoom=13), overlay(建物画像, longitude=NAGOYA[1] + 0.03,
                               width_m=300, depth_m=200))
    assert geometry["distance_m"] == pytest.approx(2700, abs=100)
    assert any("離れています" in w for w in geometry["warnings"])


# ---------------------------------------------------------------- 合成


def test_実寸どおりの大きさで貼られる(建物画像, tmp_path):
    """白い板が、間口 10.92m 相当の画素数で写る。"""
    out = tmp_path / "out.png"
    result = compose(png(colour=(0, 0, 0, 255)), spec(width=640, height=640),
                     [overlay(建物画像)], out)
    mpp = metres_per_pixel(NAGOYA[0], 20, 2)
    assert result["placements"][0]["size_px"] == (
        round(10.92 / mpp), round(6.37 / mpp))

    written = Image.open(out).convert("L")
    白 = written.point(lambda v: 255 if v > 128 else 0).getbbox()
    assert 白[2] - 白[0] == pytest.approx(round(10.92 / mpp), abs=2)
    assert 白[3] - 白[1] == pytest.approx(round(6.37 / mpp), abs=2)


def test_方位角で回る(建物画像, tmp_path):
    """90 度回せば、横長の建物は縦長に写る。"""
    out = tmp_path / "out.png"
    compose(png(colour=(0, 0, 0, 255)), spec(),
            [overlay(建物画像, bearing=90)], out)
    白 = Image.open(out).convert("L").point(
        lambda v: 255 if v > 128 else 0).getbbox()
    mpp = metres_per_pixel(NAGOYA[0], 20, 2)
    assert 白[2] - 白[0] == pytest.approx(round(6.37 / mpp), abs=3)
    assert 白[3] - 白[1] == pytest.approx(round(10.92 / mpp), abs=3)


def test_指定した緯度経度に置かれる(建物画像, tmp_path):
    """中心から東にずらしたら、写真上でも右に寄る。"""
    out = tmp_path / "out.png"
    ずらし = overlay(建物画像, longitude=NAGOYA[1] + 0.0002)
    result = compose(png(colour=(0, 0, 0, 255)), spec(), [ずらし], out)
    dx, dy = result["placements"][0]["offset_px"]
    assert dx > 0 and dy == 0

    白 = Image.open(out).convert("L").point(
        lambda v: 255 if v > 128 else 0).getbbox()
    中心x = (白[0] + 白[2]) / 2
    assert 中心x == pytest.approx(640 + dx, abs=2)  # canvas 1280 の中心 + ずれ


def test_帰属表示の帯は上書きされない(建物画像, tmp_path):
    """Google のロゴを隠すのは規約違反。建物が下端まで来ても帯は残す。"""
    band = (ATTRIBUTION_BAND * 2, (255, 0, 0, 255))
    photo = png(colour=(0, 0, 0, 255), band=band)
    out = tmp_path / "out.png"
    # 写真いっぱいの建物を、下端にかかる位置に置く。
    compose(photo, spec(zoom=21),
            [overlay(建物画像, width_m=30, depth_m=30,
                     latitude=NAGOYA[0] - 0.0002)], out)

    written = Image.open(out).convert("RGB")
    下端 = written.crop((0, written.height - band[0], written.width, written.height))
    assert 下端.getcolors() == [(下端.width * 下端.height, (255, 0, 0))]


def test_不透明度を下げると下の写真が透ける(建物画像, tmp_path):
    out = tmp_path / "out.png"
    compose(png(colour=(0, 0, 0, 255)), spec(),
            [overlay(建物画像, opacity=0.5)], out)
    written = Image.open(out).convert("L")
    assert 100 < written.getextrema()[1] < 200  # 真っ白にはならない


def test_一つでも成り立たなければ何も書かない(建物画像, tmp_path):
    """半分だけ合成された画像が残るより、何も残らないほうがよい。"""
    out = tmp_path / "out.png"
    with pytest.raises(AerialError, match="写真の外"):
        compose(png(), spec(),
                [overlay(建物画像), overlay(建物画像, latitude=NAGOYA[0] + 0.01)],
                out)
    assert not out.exists()


def test_建物の指定がなければ拒否する(tmp_path):
    with pytest.raises(AerialError, match="指定されていません"):
        compose(png(), spec(), [], tmp_path / "out.png")


# ---------------------------------------------------------------- 取得


def test_APIキーがなければ設定方法を案内する(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(AerialError, match=ENV_API_KEY):
        api_key()


def test_APIキーは環境変数から拾う(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "  k123  ")
    assert api_key() == "k123"
    assert api_key("引数優先") == "引数優先"


def test_URLにキーを出さない():
    url = map_url(spec(), "AIzaSyTESTKEY")
    assert "AIzaSyTESTKEY" in url               # 送信する URL には入っている
    assert "AIzaSyTESTKEY" not in _redacted(url)  # 表示用には出さない
    assert "key=***" in _redacted(url)


def test_PNGでなければ理由を示す():
    """キー無効・請求先未設定のとき、Static Maps は平文のエラーを返す。"""
    with pytest.raises(AerialError, match="request denied"):
        fetch_map(spec(), "k", transport=lambda url: b"The request denied")


def test_空の応答を通さない():
    with pytest.raises(AerialError, match="空"):
        fetch_map(spec(), "k", transport=lambda url: b"")


def test_取得から書き出しまで通る(建物画像, tmp_path):
    out = tmp_path / "out.png"
    受けたURL = []

    def transport(url):
        受けたURL.append(url)
        return png()

    result = render(spec(), [overlay(建物画像)], out, key="k", transport=transport)
    assert out.is_file()
    assert result["attribution"] == "地図データ ©Google"
    assert "maptype=satellite" in 受けたURL[0]
    assert "zoom=20" in 受けたURL[0]


def test_取得前に配置を確かめる(建物画像, tmp_path):
    """成り立たない指定で API を叩かない(課金される)。"""
    def transport(url):
        raise AssertionError("配置が不正なのに取得しに行った")

    with pytest.raises(AerialError, match="写真の外"):
        render(spec(), [overlay(建物画像, latitude=NAGOYA[0] + 0.01)],
               tmp_path / "out.png", key="k", transport=transport)


def test_配置図ではないと明記される(建物画像, tmp_path):
    caveat = render(spec(), [overlay(建物画像)], tmp_path / "out.png",
                    key="k", transport=lambda url: png())["caveat"]
    assert "配置図ではない" in caveat
    assert "確定測量図" in caveat
    assert "所管行政庁" in caveat
    assert "Google" in caveat


# ---------------------------------------------------------- ショートカット


def test_シンボリックリンクは画像として通る(建物画像, tmp_path):
    """OS がリンクを解決するので、実体を指していれば普通に使える。"""
    link = tmp_path / "リンク.png"
    link.symlink_to(建物画像)
    geometry = plan_overlay(spec(), overlay(link))
    assert geometry["source_px"] == (200, 100)


def test_画像でないショートカットは理由を示す(tmp_path):
    """Windows の .lnk は「画像を指す小さなファイル」で、画像ではない。

    以前はここを素通りして、合成の直前に PIL の英語例外で落ちていた。
    """
    lnk = tmp_path / "外形.png.lnk"
    lnk.write_bytes(b"L\x00\x00\x00\x01\x14\x02\x00")  # .lnk のヘッダ
    with pytest.raises(AerialError, match="ショートカット"):
        plan_overlay(spec(), overlay(lnk))


def test_壊れた画像も取得前に止まる(tmp_path, 建物画像):
    """API を叩いてから PIL が落ちると、課金だけされて何も残らない。"""
    壊れ = tmp_path / "壊れ.png"
    壊れ.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def transport(url):
        raise AssertionError("画像が読めないのに取得しに行った")

    with pytest.raises(AerialError, match="画像として読めません"):
        render(spec(), [overlay(壊れ)], tmp_path / "out.png",
               key="k", transport=transport)


def test_元画像の引き伸ばしを警告する(tmp_path):
    """粗い画像を拡大すると輪郭がぼけ、図面として読めなくなる。"""
    小さい = tmp_path / "小.png"
    Image.new("RGBA", (40, 24), (255, 255, 255, 255)).save(小さい)
    geometry = plan_overlay(spec(), overlay(小さい))
    assert any("引き伸ばします" in w for w in geometry["warnings"])
    # 元が十分大きければ黙っている。
    assert not any("引き伸ばします" in w
                   for w in plan_overlay(spec(), overlay(建物画像_大(tmp_path)))["warnings"])


def 建物画像_大(tmp_path):
    path = tmp_path / "大.png"
    Image.new("RGBA", (600, 350), (255, 255, 255, 255)).save(path)
    return path
