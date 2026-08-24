"""3D 外観（マッシング）生成。

各階の footprint を押し出して積層し、屋根（切妻／陸屋根）を載せた
単純なソリッドを作る。Wavefront OBJ と、確認用のアイソメ SVG を出力できる。
BIM/IFC 出力もこのマッシングを土台にする。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .geometry import Point, Polygon, bbox, centroid
from .models import Building

Vertex = Tuple[float, float, float]
Face = List[int]  # OBJ と同じく 1 始まりのインデックス


@dataclass
class Mesh:
    vertices: List[Vertex] = field(default_factory=list)
    faces: List[Face] = field(default_factory=list)
    groups: Dict[str, List[int]] = field(default_factory=dict)  # グループ名 -> 面インデックス

    def add_vertex(self, v: Vertex) -> int:
        self.vertices.append(v)
        return len(self.vertices)  # OBJ は 1 始まり

    def add_face(self, indices: Sequence[int], group: str = "default") -> None:
        self.faces.append(list(indices))
        self.groups.setdefault(group, []).append(len(self.faces) - 1)

    def to_obj(self, name: str = "building") -> str:
        lines = [f"# {name} - AI LAND DESIGN massing", "# 単位: m"]
        for x, y, z in self.vertices:
            # OBJ は y-up が慣例のため、平面 y を z に、高さを y に割り当てる。
            lines.append(f"v {x:.4f} {z:.4f} {-y:.4f}")
        for group, face_ids in self.groups.items():
            lines.append(f"g {group}")
            for fid in face_ids:
                lines.append("f " + " ".join(str(i) for i in self.faces[fid]))
        return "\n".join(lines) + "\n"


def _prism(mesh: Mesh, polygon: Sequence[Point], z0: float, z1: float, group: str) -> None:
    """ポリゴンを z0..z1 に押し出して側面・上下面を張る。"""
    base = [mesh.add_vertex((x, y, z0)) for x, y in polygon]
    top = [mesh.add_vertex((x, y, z1)) for x, y in polygon]
    n = len(polygon)
    for i in range(n):
        j = (i + 1) % n
        mesh.add_face([base[i], base[j], top[j], top[i]], group)
    mesh.add_face(list(reversed(base)), f"{group}_底")
    mesh.add_face(top, f"{group}_天")


def build_massing(building: Building, roof_pitch: float = 0.4) -> Mesh:
    """建物マッシングを生成する。"""
    mesh = Mesh()
    z = 0.0
    for floor in building.floors:
        _prism(mesh, floor.footprint, z, z + floor.height_m, f"{floor.storey}階")
        z += floor.height_m

    if not building.floors:
        return mesh

    top = building.floors[-1].footprint
    min_x, min_y, max_x, max_y = bbox(top)
    depth = max_y - min_y
    ridge_h = depth / 2 * roof_pitch if building.roof == "切妻" else 0.0

    if building.roof == "切妻" and ridge_h > 0:
        # 東西方向に棟を持つ切妻屋根
        eave = [
            mesh.add_vertex((min_x, min_y, z)),
            mesh.add_vertex((max_x, min_y, z)),
            mesh.add_vertex((max_x, max_y, z)),
            mesh.add_vertex((min_x, max_y, z)),
        ]
        ridge_y = (min_y + max_y) / 2
        r0 = mesh.add_vertex((min_x, ridge_y, z + ridge_h))
        r1 = mesh.add_vertex((max_x, ridge_y, z + ridge_h))
        mesh.add_face([eave[0], eave[1], r1, r0], "屋根_南")
        mesh.add_face([eave[2], eave[3], r0, r1], "屋根_北")
        mesh.add_face([eave[1], eave[2], r1], "妻壁_東")
        mesh.add_face([eave[3], eave[0], r0], "妻壁_西")
    else:
        _prism(mesh, top, z, z + 0.3, "陸屋根パラペット")
    return mesh


def total_height_m(building: Building, roof_pitch: float = 0.4) -> float:
    """屋根頂部までの高さ [m]。"""
    if not building.floors:
        return 0.0
    z = sum(f.height_m for f in building.floors)
    top = building.floors[-1].footprint
    min_x, min_y, max_x, max_y = bbox(top)
    if building.roof == "切妻":
        return z + (max_y - min_y) / 2 * roof_pitch
    return z + 0.3


def to_svg(building: Building, scale: float = 14.0, roof_pitch: float = 0.4) -> str:
    """アイソメ図（斜投影）の SVG。外観ボリュームの確認用。"""
    mesh = build_massing(building, roof_pitch)
    if not mesh.vertices:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'

    cos30, sin30 = math.cos(math.radians(30)), math.sin(math.radians(30))

    def project(v: Vertex) -> Tuple[float, float]:
        x, y, z = v
        return ((x - y) * cos30, (x + y) * sin30 - z)

    pts = [project(v) for v in mesh.vertices]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    margin = 2.0
    width = (max(xs) - min(xs) + margin * 2) * scale
    height = (max(ys) - min(ys) + margin * 2) * scale

    def px(p: Tuple[float, float]) -> Tuple[float, float]:
        return ((p[0] - min(xs) + margin) * scale, (p[1] - min(ys) + margin) * scale)

    palette = {"屋根": "#8c5a3c", "妻壁": "#d8cfc0", "階": "#efe9dd"}

    def color_for(group: str) -> str:
        for key, value in palette.items():
            if group.startswith(key) or key in group:
                return value
        return "#e5e0d6"

    # 奥のものから描くため、面の重心の奥行き（x+y）でソートする。
    order: List[Tuple[float, int, str]] = []
    for group, face_ids in mesh.groups.items():
        if group.endswith("_底"):
            continue
        for fid in face_ids:
            face = mesh.faces[fid]
            depth = sum(mesh.vertices[i - 1][0] + mesh.vertices[i - 1][1] for i in face) / len(face)
            order.append((depth, fid, group))
    order.sort(key=lambda t: t[0])

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    for _, fid, group in order:
        face = mesh.faces[fid]
        pointstr = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (px(pts[i - 1]) for i in face)
        )
        parts.append(
            f'<polygon points="{pointstr}" fill="{color_for(group)}" stroke="#3a3a3a" '
            'stroke-width="1" stroke-linejoin="round"/>'
        )
    parts.append(
        f'<text x="8" y="18" font-size="13" font-family="sans-serif" fill="#111111">'
        f'外観イメージ　{building.structure.value} {building.storeys}階建 / '
        f'最高高さ {total_height_m(building, roof_pitch):.2f}m</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)
