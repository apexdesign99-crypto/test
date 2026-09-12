"""航空写真に建物を実寸で重ねる。

やっていること:
  指定した緯度経度・方位角・実寸(m)に合わせて建物画像を拡大縮小・回転し、
  航空写真の正しい画素位置に合成する。縮尺は Web メルカトルの
  緯度補正を入れて計算する。

やっていないこと:
  敷地境界の判定。地番と敷地形状の照合。建ぺい率・斜線・日影の検討。
  **出力は検討用のイメージであって、配置図ではない。**
  敷地の形と位置は公図・確定測量図で確認すること。

**Google の航空写真は Google のコンテンツである。**
帰属表示(左下のロゴと右下のクレジット)は合成後も必ず残す。
このモジュールは帯を上書きから守るが、規約に適合するかの判断は
利用者が Google Maps Platform の利用規約(一次情報)で行うこと。
"""

from __future__ import annotations

import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"

# API キーを環境変数で渡す場合の名前。**キーは資格情報**なので画面にもログにも出さない。
ENV_API_KEY = "GOOGLE_MAPS_API_KEY"

# Web メルカトルの 1 タイル。ズーム 0 の世界全体がこの画素数に収まる。
TILE_SIZE = 256

# 赤道での地球一周(m)。縮尺の基準。
EARTH_CIRCUMFERENCE = 40_075_016.686

# Web メルカトルが表現できる緯度の限界。
MAX_LATITUDE = 85.05112878

# Static Maps API が 1 辺に許す最大画素数(標準プラン)。
MAX_SIZE = 640

# 左下のロゴと右下のクレジットが載る帯の高さ(scale=1 のとき)。
# 合成後にこの帯を元の写真で塗り直し、帰属表示が隠れないようにする。
ATTRIBUTION_BAND = 26

# 建物がこれ未満の画素数にしかならないなら、重ねても読めない。
MIN_BUILDING_PIXELS = 8

# 地図の中心からこれ以上離れた建物は、指定間違いを疑う。
FAR_FROM_CENTRE_M = 2_000

# 元画像をこれ以上引き伸ばすと、輪郭がぼけて図面として読めなくなる。
UPSCALE_WARNING = 1.5

ATTRIBUTION = "地図データ ©Google"


class AerialError(RuntimeError):
    """航空写真の取得・合成に失敗した。"""


# ------------------------------------------------------------------ 座標計算


def metres_per_pixel(latitude: float, zoom: int, scale: int = 1) -> float:
    """その緯度・ズームでの 1 画素あたりの地上距離(m)。

    Web メルカトルは緯度が上がるほど引き伸ばされる。cos を掛け忘れると
    北緯 35 度で 22% 大きい建物を描いてしまう。
    """
    at_equator = EARTH_CIRCUMFERENCE / (TILE_SIZE * 2 ** zoom)
    return at_equator * math.cos(math.radians(latitude)) / scale


def _world_xy(latitude: float, longitude: float) -> tuple[float, float]:
    """緯度経度を Web メルカトルのワールド座標(ズーム 0 の 256 画素系)に写す。

    画素のずれはこの座標系の差から出す。metres_per_pixel を使った
    近似では、地図の端に行くほど誤差が積もる。
    """
    siny = math.sin(math.radians(latitude))
    siny = min(max(siny, -0.9999), 0.9999)
    x = TILE_SIZE * (0.5 + longitude / 360)
    y = TILE_SIZE * (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi))
    return x, y


def pixel_offset(
    centre: tuple[float, float],
    point: tuple[float, float],
    zoom: int,
    scale: int = 1,
) -> tuple[float, float]:
    """地図中心から見た点の画素ずれ。右が +x、下が +y。"""
    cx, cy = _world_xy(*centre)
    px, py = _world_xy(*point)
    factor = (2 ** zoom) * scale
    return (px - cx) * factor, (py - cy) * factor


def ground_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2 点間の概算距離(m)。指定間違いの検出にだけ使う。"""
    radius = EARTH_CIRCUMFERENCE / (2 * math.pi)
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


# ------------------------------------------------------------------ 入力の型


def _check_coordinates(latitude: float, longitude: float, label: str) -> None:
    if not -MAX_LATITUDE <= latitude <= MAX_LATITUDE:
        raise AerialError(
            f"{label}の緯度 {latitude} は範囲外です"
            f"(-{MAX_LATITUDE}〜{MAX_LATITUDE})。緯度と経度が逆かもしれません。"
        )
    if not -180 <= longitude <= 180:
        raise AerialError(f"{label}の経度 {longitude} は範囲外です(-180〜180)。")


@dataclass
class MapSpec:
    """取得する航空写真の指定。"""

    latitude: float
    longitude: float
    zoom: int = 19
    width: int = 640
    height: int = 640
    scale: int = 2
    maptype: str = "satellite"

    def __post_init__(self) -> None:
        _check_coordinates(self.latitude, self.longitude, "地図中心")
        if not 0 <= self.zoom <= 21:
            raise AerialError(f"ズーム {self.zoom} は範囲外です(0〜21)。")
        if not 1 <= self.width <= MAX_SIZE or not 1 <= self.height <= MAX_SIZE:
            raise AerialError(
                f"画像サイズは 1〜{MAX_SIZE} 画素です(指定 {self.width}×{self.height})。"
                f"広く見たいときは zoom を下げてください。"
            )
        if self.scale not in (1, 2):
            raise AerialError(f"scale は 1 か 2 です(指定 {self.scale})。")
        if self.maptype not in ("satellite", "hybrid", "roadmap", "terrain"):
            raise AerialError(f"maptype {self.maptype!r} は指定できません。")

    @property
    def canvas(self) -> tuple[int, int]:
        return self.width * self.scale, self.height * self.scale

    @property
    def centre(self) -> tuple[float, float]:
        return self.latitude, self.longitude


@dataclass
class Overlay:
    """重ねる建物の指定。

    画像は「上が真北・横が間口」の向きで用意し、bearing で回す。
    画像の余白は透過(RGBA)にしておくこと。白地のままだと敷地が隠れる。
    """

    image: str | Path
    width_m: float          # 間口(画像の横方向の実寸)
    depth_m: float          # 奥行(画像の縦方向の実寸)
    latitude: float
    longitude: float
    bearing: float = 0.0    # 画像の上端が真北から時計回りに何度か
    opacity: float = 1.0

    def __post_init__(self) -> None:
        _check_coordinates(self.latitude, self.longitude, "建物中心")
        for name, value in (("間口", self.width_m), ("奥行", self.depth_m)):
            if not value > 0:
                raise AerialError(f"{name}は正の数で指定してください(指定 {value})。")
        if not 0 <= self.opacity <= 1:
            raise AerialError(f"不透明度は 0〜1 です(指定 {self.opacity})。")
        self.bearing = float(self.bearing) % 360
        self.image = Path(self.image)
        if not self.image.is_file():
            raise AerialError(f"重ねる画像が見つかりません: {self.image}")


# ------------------------------------------------------------------ 取得


def _redacted(url: str) -> str:
    """API キーを伏せた URL。画面にもログにもキーを出さない。"""
    return urllib.parse.urlunparse(
        urllib.parse.urlparse(url)._replace(
            query="&".join(
                f"{k}=***" if k == "key" else f"{k}={v}"
                for k, v in urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)
            )
        )
    )


def api_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get(ENV_API_KEY, "")
    if not key.strip():
        raise AerialError(
            f"Google Maps の API キーがありません。環境変数 {ENV_API_KEY} に設定するか "
            f"--api-key で渡してください。Static Maps API の有効化も必要です。"
        )
    return key.strip()


def map_url(spec: MapSpec, key: str) -> str:
    query = urllib.parse.urlencode({
        "center": f"{spec.latitude},{spec.longitude}",
        "zoom": spec.zoom,
        "size": f"{spec.width}x{spec.height}",
        "scale": spec.scale,
        "maptype": spec.maptype,
        "format": "png",
        "key": key,
    })
    return f"{STATIC_MAPS_URL}?{query}"


def http_get_bytes(url: str) -> bytes:
    """実際に GET を投げる。テストではここを差し替える。"""
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        # Static Maps はエラー理由を本文の平文で返す(キー無効・請求先未設定など)。
        raise AerialError(
            f"航空写真を取得できません (HTTP {exc.code}): {body or exc.reason}"
        ) from None
    except urllib.error.URLError as exc:
        raise AerialError(f"Google に接続できません: {exc.reason}") from None


def fetch_map(
    spec: MapSpec,
    key: str,
    transport: Callable[[str], bytes] | None = None,
) -> bytes:
    data = (transport or http_get_bytes)(map_url(spec, key))
    if not data:
        raise AerialError("航空写真の応答が空でした。")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        head = data[:200].decode("utf-8", errors="replace").strip()
        raise AerialError(f"PNG が返りませんでした: {head}")
    return data


# ------------------------------------------------------------------ 合成


def _pillow():
    try:
        from PIL import Image
    except ModuleNotFoundError:
        raise AerialError(
            "Pillow が必要です。pip install Pillow を実行してください。"
        ) from None
    return Image


def _open_art(path: Path):
    """重ねる画像を開く。開けない理由を日本語で返す。

    シンボリックリンクは OS が解決するのでそのまま通る。一方、Windows の
    ショートカット(.lnk)と macOS のエイリアスは「画像を指す小さなファイル」
    であって画像ではないため、ここで止まる。
    """
    Image = _pillow()
    try:
        return Image.open(path)
    except Exception:
        raise AerialError(
            f"画像として読めません: {path}。"
            f"Windows のショートカット(.lnk)や macOS のエイリアスは画像そのもの"
            f"ではないので使えません。PNG の実体を指定するか、"
            f"シンボリックリンク(ln -s / mklink)にしてください。"
        ) from None


def plan_overlay(spec: MapSpec, overlay: Overlay) -> dict[str, Any]:
    """合成せずに、その建物がどこに何画素で載るかだけを計算する。

    取得の前に確かめられるようにしておく。API を叩いてから
    「小さすぎて読めない」と分かるのは無駄。
    """
    source = _open_art(overlay.image).size
    mpp = metres_per_pixel(overlay.latitude, spec.zoom, spec.scale)
    width_px = overlay.width_m / mpp
    depth_px = overlay.depth_m / mpp
    dx, dy = pixel_offset(spec.centre, (overlay.latitude, overlay.longitude),
                          spec.zoom, spec.scale)
    canvas_w, canvas_h = spec.canvas
    centre_px = (canvas_w / 2 + dx, canvas_h / 2 + dy)

    warnings: list[str] = []
    if min(width_px, depth_px) < MIN_BUILDING_PIXELS:
        raise AerialError(
            f"この縮尺では建物が {width_px:.0f}×{depth_px:.0f} 画素にしかなりません。"
            f"zoom を上げるか scale=2 にしてください(現在 zoom={spec.zoom})。"
        )
    if max(width_px, depth_px) > max(canvas_w, canvas_h) * 1.5:
        warnings.append(
            f"建物({width_px:.0f}×{depth_px:.0f}画素)が写真からはみ出します。"
            f"zoom を下げてください。"
        )
    radius = math.hypot(width_px, depth_px) / 2
    if (centre_px[0] + radius < 0 or centre_px[0] - radius > canvas_w
            or centre_px[1] + radius < 0 or centre_px[1] - radius > canvas_h):
        raise AerialError(
            "建物の位置が写真の外です。緯度経度か zoom を見直してください"
            f"(中心からのずれ {dx:.0f}, {dy:.0f} 画素)。"
        )
    away = ground_distance(spec.centre, (overlay.latitude, overlay.longitude))
    if away > FAR_FROM_CENTRE_M:
        warnings.append(
            f"建物が地図中心から約 {away / 1000:.1f}km 離れています。"
            f"緯度経度の取り違えを疑ってください。"
        )
    if max(width_px / source[0], depth_px / source[1]) > UPSCALE_WARNING:
        warnings.append(
            f"元画像 {source[0]}×{source[1]} を "
            f"{width_px:.0f}×{depth_px:.0f} に引き伸ばします。輪郭がぼけます。"
        )
    return {
        "metres_per_pixel": mpp,
        "source_px": source,
        "size_px": (width_px, depth_px),
        "offset_px": (dx, dy),
        "centre_px": centre_px,
        "distance_m": away,
        "warnings": warnings,
    }


def _place(canvas, overlay: Overlay, geometry: dict[str, Any], Image) -> None:
    art = _open_art(overlay.image).convert("RGBA")
    width_px, depth_px = geometry["size_px"]
    art = art.resize((max(1, round(width_px)), max(1, round(depth_px))),
                     Image.LANCZOS)
    if overlay.bearing:
        # PIL の rotate は反時計回り。方位角は時計回りなので符号を反転する。
        art = art.rotate(-overlay.bearing, resample=Image.BICUBIC, expand=True)
    if overlay.opacity < 1:
        alpha = art.getchannel("A").point(lambda v: round(v * overlay.opacity))
        art.putalpha(alpha)
    cx, cy = geometry["centre_px"]
    canvas.alpha_composite(art, (round(cx - art.width / 2), round(cy - art.height / 2)))


def compose(
    photo: bytes,
    spec: MapSpec,
    overlays: list[Overlay],
    destination: str | Path,
) -> dict[str, Any]:
    """航空写真に建物を重ねて PNG で書き出す。"""
    if not overlays:
        raise AerialError("重ねる建物が指定されていません。")
    Image = _pillow()
    import io

    # 先に全部の配置を確かめる。1 つでも成り立たないなら 1 枚も書かない。
    plans = [plan_overlay(spec, o) for o in overlays]

    base = Image.open(io.BytesIO(photo)).convert("RGBA")
    if base.size != spec.canvas:
        # 画素サイズが指定と違うと、緯度経度から出した位置がそのままずれる。
        # 「だいたい合っている絵」が一番危ないので、黙って続けない。
        raise AerialError(
            f"写真の画素サイズ {base.width}×{base.height} が指定 "
            f"{spec.canvas[0]}×{spec.canvas[1]} と違います。"
            f"size と scale の指定を確かめてください。"
        )
    canvas = base.copy()
    for overlay, geometry in zip(overlays, plans):
        _place(canvas, overlay, geometry, Image)

    # 帰属表示の帯を元の写真で塗り直す。Google のロゴを隠すと規約に反する。
    band = ATTRIBUTION_BAND * spec.scale
    if band < base.height:
        strip = base.crop((0, base.height - band, base.width, base.height))
        canvas.paste(strip, (0, base.height - band))

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(destination, "PNG")

    return {
        "path": str(destination),
        "canvas": spec.canvas,
        "metres_per_pixel": plans[0]["metres_per_pixel"],
        "placements": [
            {
                "image": str(o.image),
                "size_px": tuple(round(v) for v in g["size_px"]),
                "offset_px": tuple(round(v) for v in g["offset_px"]),
                "bearing": o.bearing,
            }
            for o, g in zip(overlays, plans)
        ],
        "warnings": [w for g in plans for w in g["warnings"]],
        "attribution": ATTRIBUTION,
        "caveat": (
            "この画像は検討用のイメージであり、配置図ではない。"
            "敷地境界・地番・道路境界は公図と確定測量図で確認すること。"
            "航空写真は撮影時点のもので、現況とは異なる。"
            "建ぺい率・容積率・斜線制限・日影規制の適否は判断していない。"
            "計画地に適用される規制は所管行政庁と都市計画情報で確認すること。"
            f"地図画像の権利は Google にある({ATTRIBUTION})。"
            "帰属表示を消さずに使い、利用範囲は Google Maps Platform の"
            "利用規約(一次情報)で確認すること。"
        ),
    }


def render(
    spec: MapSpec,
    overlays: list[Overlay],
    destination: str | Path,
    key: str | None = None,
    transport: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    """写真を取ってきて合成するところまで。"""
    if not overlays:
        raise AerialError("重ねる建物が指定されていません。")
    # 取得の前に配置を検算する。API を叩いてから駄目だと分かるのは無駄。
    for overlay in overlays:
        plan_overlay(spec, overlay)
    photo = fetch_map(spec, api_key(key), transport)
    return compose(photo, spec, overlays, destination)
