"""IFC4 (STEP / ISO-10303-21) 出力。

生成した建物案を BIM に受け渡すためのエクスポータ。外部ライブラリを使わず、
IFC4 の必要最小限のエンティティを直接書き出す。

出力する構成:
    IfcProject
      └ IfcSite
          └ IfcBuilding
              └ IfcBuildingStorey (各階)
                  ├ IfcSlab            床スラブ
                  ├ IfcWallStandardCase 外壁（各階4面）
                  └ IfcSpace           各室（間取り）

GlobalId は要素キーからの UUID5 で決定的に生成するため、同じ入力からは
同じ IFC が得られる（差分確認・テストが可能）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from ..geometry import Point, Polygon, bbox
from ..models import Building, Site

_GUID_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

#: 床スラブ厚 [m]
SLAB_THICKNESS_M = 0.2
#: 外壁厚 [m]
WALL_THICKNESS_M = 0.15


def compress_guid(value: uuid.UUID) -> str:
    """UUID を IFC の 22 文字 GlobalId に変換する。"""
    num = value.int
    chars: List[str] = []
    for _ in range(22):
        num, rem = divmod(num, 64)
        chars.append(_GUID_CHARS[rem])
    return "".join(reversed(chars))


def guid_for(key: str) -> str:
    return compress_guid(uuid.uuid5(_NAMESPACE, key))


def ifc_string(value: str) -> str:
    """STEP 文字列リテラル。非 ASCII は IFC の \\X2\\ エンコードにする。"""
    out: List[str] = []
    buffer: List[str] = []

    def flush() -> None:
        if buffer:
            out.append("\\X2\\" + "".join(buffer) + "\\X0\\")
            buffer.clear()

    for ch in value:
        if ord(ch) < 128:
            flush()
            out.append("''" if ch == "'" else ("\\\\" if ch == "\\" else ch))
        else:
            buffer.append(ch.encode("utf-16-be").hex().upper())
    flush()
    return "'" + "".join(out) + "'"


def num(value: float) -> str:
    """STEP の REAL 表記（必ず小数点を含む）。"""
    return f"{value:.6f}"


class _StepFile:
    """STEP エンティティを採番しながら積み上げるバッファ。"""

    def __init__(self) -> None:
        self._lines: List[str] = []
        self._counter = 0

    def add(self, entity: str) -> str:
        self._counter += 1
        ref = f"#{self._counter}"
        self._lines.append(f"{ref}= {entity};")
        return ref

    @property
    def body(self) -> str:
        return "\n".join(self._lines)


def _list(refs: Iterable[str]) -> str:
    return "(" + ",".join(refs) + ")"


def _profile(step: _StepFile, polygon: Sequence[Point], name: str) -> str:
    """閉じたポリゴンから IfcArbitraryClosedProfileDef を作る。"""
    points = list(polygon) + [polygon[0]]
    refs = [step.add(f"IFCCARTESIANPOINT(({num(x)},{num(y)}))") for x, y in points]
    polyline = step.add(f"IFCPOLYLINE({_list(refs)})")
    return step.add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,{ifc_string(name)},{polyline})")


def _extruded_solid(step: _StepFile, profile: str, z: float, depth: float) -> str:
    origin = step.add(f"IFCCARTESIANPOINT(({num(0)},{num(0)},{num(z)}))")
    placement = step.add(f"IFCAXIS2PLACEMENT3D({origin},$,$)")
    direction = step.add("IFCDIRECTION((0.,0.,1.))")
    return step.add(
        f"IFCEXTRUDEDAREASOLID({profile},{placement},{direction},{num(max(depth, 0.001))})"
    )


def _shape(step: _StepFile, context: str, solid: str) -> str:
    representation = step.add(
        f"IFCSHAPEREPRESENTATION({context},'Body','SweptSolid',({solid}))"
    )
    return step.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,({representation}))")


def _placement(step: _StepFile, parent: Optional[str], z: float = 0.0) -> str:
    origin = step.add(f"IFCCARTESIANPOINT(({num(0)},{num(0)},{num(z)}))")
    axis = step.add(f"IFCAXIS2PLACEMENT3D({origin},$,$)")
    return step.add(f"IFCLOCALPLACEMENT({parent or '$'},{axis})")


def _wall_polygons(footprint: Sequence[Point], thickness: float) -> List[Tuple[str, Polygon]]:
    """外周に沿った 4 枚の壁（内側にオフセットした帯）を返す。"""
    min_x, min_y, max_x, max_y = bbox(footprint)
    t = thickness
    return [
        ("南面", [(min_x, min_y), (max_x, min_y), (max_x, min_y + t), (min_x, min_y + t)]),
        ("北面", [(min_x, max_y - t), (max_x, max_y - t), (max_x, max_y), (min_x, max_y)]),
        ("西面", [(min_x, min_y + t), (min_x + t, min_y + t), (min_x + t, max_y - t), (min_x, max_y - t)]),
        ("東面", [(max_x - t, min_y + t), (max_x, min_y + t), (max_x, max_y - t), (max_x - t, max_y - t)]),
    ]


def to_ifc(
    site: Site,
    building: Building,
    project_name: str = "AI LAND DESIGN",
    author: str = "AI LAND DESIGN",
    organization: str = "AI LAND DESIGN",
    timestamp: Optional[datetime] = None,
) -> str:
    """建物案を IFC4 の STEP 文字列に変換する。"""
    step = _StepFile()
    now = timestamp or datetime.now(timezone.utc)

    person = step.add(f"IFCPERSON($,{ifc_string(author)},$,$,$,$,$,$)")
    org = step.add(f"IFCORGANIZATION($,{ifc_string(organization)},$,$,$)")
    person_org = step.add(f"IFCPERSONANDORGANIZATION({person},{org},$)")
    application = step.add(
        f"IFCAPPLICATION({org},'0.1',{ifc_string('AI LAND DESIGN pipeline')},'ai-land-design')"
    )
    owner = step.add(
        f"IFCOWNERHISTORY({person_org},{application},$,.ADDED.,$,$,$,{int(now.timestamp())})"
    )

    length = step.add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
    area_unit = step.add("IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.)")
    volume = step.add("IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.)")
    angle = step.add("IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.)")
    units = step.add(f"IFCUNITASSIGNMENT({_list([length, area_unit, volume, angle])})")

    origin = step.add(f"IFCCARTESIANPOINT(({num(0)},{num(0)},{num(0)}))")
    world = step.add(f"IFCAXIS2PLACEMENT3D({origin},$,$)")
    true_north = step.add("IFCDIRECTION((0.,1.))")
    context = step.add(
        f"IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.000000E-5,{world},{true_north})"
    )

    project = step.add(
        f"IFCPROJECT('{guid_for('project:' + site.site_id)}',{owner},"
        f"{ifc_string(project_name)},$,$,$,$,({context}),{units})"
    )

    site_placement = _placement(step, None)
    site_ref = step.add(
        f"IFCSITE('{guid_for('site:' + site.site_id)}',{owner},{ifc_string(site.address or site.site_id)},"
        f"$,$,{site_placement},$,$,.ELEMENT.,$,$,$,$,$)"
    )
    building_placement = _placement(step, site_placement)
    building_ref = step.add(
        f"IFCBUILDING('{guid_for('building:' + site.site_id)}',{owner},"
        f"{ifc_string(building.ldk_type + ' ' + building.structure.value)},$,$,"
        f"{building_placement},$,$,.ELEMENT.,$,$,$)"
    )

    step.add(
        f"IFCRELAGGREGATES('{guid_for('agg:project:' + site.site_id)}',{owner},$,$,{project},({site_ref}))"
    )
    step.add(
        f"IFCRELAGGREGATES('{guid_for('agg:site:' + site.site_id)}',{owner},$,$,{site_ref},({building_ref}))"
    )

    storey_refs: List[str] = []
    elevation = 0.0
    for floor in building.floors:
        key = f"{site.site_id}:storey{floor.storey}"
        storey_placement = _placement(step, building_placement, elevation)
        storey_ref = step.add(
            f"IFCBUILDINGSTOREY('{guid_for(key)}',{owner},{ifc_string(f'{floor.storey}階')},$,$,"
            f"{storey_placement},$,$,.ELEMENT.,{num(elevation)})"
        )
        storey_refs.append(storey_ref)

        elements: List[str] = []

        slab_profile = _profile(step, floor.footprint, f"{floor.storey}階 床")
        slab_solid = _extruded_solid(step, slab_profile, 0.0, SLAB_THICKNESS_M)
        slab_shape = _shape(step, context, slab_solid)
        slab_placement = _placement(step, storey_placement)
        elements.append(
            step.add(
                f"IFCSLAB('{guid_for(key + ':slab')}',{owner},{ifc_string(f'{floor.storey}階 床スラブ')},"
                f"$,$,{slab_placement},{slab_shape},$,.FLOOR.)"
            )
        )

        wall_height = max(0.1, floor.height_m - SLAB_THICKNESS_M)
        for name, polygon in _wall_polygons(floor.footprint, WALL_THICKNESS_M):
            wall_profile = _profile(step, polygon, f"{floor.storey}階 外壁{name}")
            wall_solid = _extruded_solid(step, wall_profile, SLAB_THICKNESS_M, wall_height)
            wall_shape = _shape(step, context, wall_solid)
            wall_placement = _placement(step, storey_placement)
            elements.append(
                step.add(
                    f"IFCWALLSTANDARDCASE('{guid_for(key + ':wall:' + name)}',{owner},"
                    f"{ifc_string(f'{floor.storey}階 外壁 {name}')},$,$,{wall_placement},"
                    f"{wall_shape},$,.STANDARD.)"
                )
            )

        space_refs: List[str] = []
        for index, room in enumerate(floor.rooms):
            polygon = [
                (room.x, room.y),
                (room.x + room.w, room.y),
                (room.x + room.w, room.y + room.h),
                (room.x, room.y + room.h),
            ]
            space_profile = _profile(step, polygon, room.name)
            space_solid = _extruded_solid(step, space_profile, SLAB_THICKNESS_M, wall_height)
            space_shape = _shape(step, context, space_solid)
            space_placement = _placement(step, storey_placement)
            space_refs.append(
                step.add(
                    f"IFCSPACE('{guid_for(f'{key}:space{index}')}',{owner},{ifc_string(room.name)},"
                    f"{ifc_string(f'{room.area_m2:.2f} m2')},$,{space_placement},{space_shape},$,"
                    f".ELEMENT.,.INTERNAL.,{num(elevation)})"
                )
            )

        if elements:
            step.add(
                f"IFCRELCONTAINEDINSPATIALSTRUCTURE('{guid_for(key + ':contains')}',{owner},$,$,"
                f"{_list(elements)},{storey_ref})"
            )
        if space_refs:
            step.add(
                f"IFCRELAGGREGATES('{guid_for(key + ':spaces')}',{owner},$,$,{storey_ref},"
                f"{_list(space_refs)})"
            )
        elevation += floor.height_m

    if storey_refs:
        step.add(
            f"IFCRELAGGREGATES('{guid_for('agg:building:' + site.site_id)}',{owner},$,$,"
            f"{building_ref},{_list(storey_refs)})"
        )

    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    header = "\n".join(
        [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');",
            f"FILE_NAME({ifc_string(site.site_id + '.ifc')},'{stamp}',"
            f"({ifc_string(author)}),({ifc_string(organization)}),"
            f"{ifc_string('ai-land-design')},{ifc_string('ai-land-design')},$);",
            "FILE_SCHEMA(('IFC4'));",
            "ENDSEC;",
            "DATA;",
        ]
    )
    return f"{header}\n{step.body}\nENDSEC;\nEND-ISO-10303-21;\n"


def write_ifc(path: str | Path, site: Site, building: Building, **kwargs) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_ifc(site, building, **kwargs), encoding="utf-8")
    return target
