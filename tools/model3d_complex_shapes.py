"""
model3d_complex_shapes.py
==========================
Utökning av model3d_tools.py: verktyg för att skapa GENUINT komplexa former,
inte bara box/cylinder/sphere/cone + parvisa booleska operationer.

De befintliga verktygen (create_3d_model, combine_3d_models, create_case_shell,
create_bracket, create_flat_plate) täcker enkla/rätvinkliga former bra, men
klarar inte:
    - fria/organiska konturer (stjärnor, logotyper, oregelbundna profiler)
    - koniska/vridna former (avsmalnande, twistade former)
    - former som ändrar tvärsnitt längs sin höjd (flaskor, vaser, handtag)
    - runda/organiska övergångar mellan hårda CSG-kanter
    - att bygga en modell av MÅNGA primitiver i ETT anrop istället för att
      spara en fil per steg och kedja combine_3d_models manuellt

Fem nya verktyg täcker det:
    extrude_custom_profile  - fri 2D-kontur, extruderad, med valfri
                               avsmalning (taper) och vridning (twist)
    revolve_profile          - "svarvad" form (vas, flaska, handtag, fot) —
                               en radie-profil roterad 360° runt Z-axeln
    loft_profiles             - lofta mellan valfritt antal 2D-profiler vid
                               olika höjder — den mest generella av alla,
                               t.ex. stjärnformad bas som övergår i en
                               cirkulär topp
    smooth_mesh               - Taubin-utjämning (+ valfri subdivision) för
                               att runda av skarpa CSG-kanter till en
                               organisk yta, på VILKEN modell som helst
    compose_shapes             - bygg en hel sammansatt del (flera
                               primitiver + booleska operationer + egna
                               transformer) i ETT verktygsanrop istället för
                               att kedja combine_3d_models flera gånger

Alla verktyg återanvänder MODELS_DIR, _geometry_report och materialdatabasen
från model3d_tools.py, och stödjer export till stl/obj/3mf (3mf stöds
direkt av trimesh >=4, ingen extra beroende behövs).

Beroenden utöver model3d_tools.py: mapbox_earcut (för polygon-triangulering
av lock/botten i loft/extrude): pip install --break-system-packages mapbox_earcut
"""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from shapely.geometry import Polygon

from tools.model3d_tools import MODELS_DIR, _geometry_report, _load_materials

VALID_FORMATS = ("stl", "obj", "3mf")


def _export(mesh: "trimesh.Trimesh", name: str, export_format: str) -> Path:
    if export_format not in VALID_FORMATS:
        raise ValueError(f"export_format måste vara en av: {', '.join(VALID_FORMATS)}")
    fpath = MODELS_DIR / f"{name}.{export_format}"
    mesh.export(fpath)
    return fpath


def _resample_ring(points: list, n: int) -> np.ndarray:
    """Jämnt fördelade punkter längs en sluten polygons omkrets (arc-length),
    så att två profiler med olika antal originalpunkter kan loftas mot
    varandra rad för rad."""
    pts = np.array(points, dtype=float)
    if not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[0]])
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    if np.any(seg <= 0):
        pts = pts[np.concatenate([[True], seg > 0])]
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0:
        raise ValueError("Profilen har ingen omkrets (alla punkter identiska?).")
    targets = np.linspace(0, total, n, endpoint=False)
    out = []
    for t in targets:
        idx = min(np.searchsorted(cum, t, side="right") - 1, len(seg) - 1)
        t0, t1 = cum[idx], cum[idx + 1]
        frac = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
        out.append(pts[idx] + frac * (pts[idx + 1] - pts[idx]))
    return np.array(out)


def _triangulate_cap(ring: np.ndarray, z: float, flip: bool = False) -> tuple[np.ndarray, np.ndarray]:
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    v2d, faces = trimesh.creation.triangulate_polygon(poly, engine="earcut")
    verts3d = np.column_stack([v2d, np.full(len(v2d), z)])
    if flip:
        faces = faces[:, ::-1]
    return verts3d, faces


def _loft_mesh(profiles: list[tuple[list, float]], sections: int = 48) -> "trimesh.Trimesh":
    """profiles: lista av (points[[x,y],...], z_mm), sorterad efter z."""
    if len(profiles) < 2:
        raise ValueError("loft kräver minst 2 profiler (t.ex. botten + topp).")
    rings = [_resample_ring(pts, sections) for pts, _ in profiles]
    zs = [z for _, z in profiles]

    verts = []
    for ring, z in zip(rings, zs):
        for x, y in ring:
            verts.append([x, y, z])
    verts = np.array(verts)

    faces = []
    for li in range(len(profiles) - 1):
        base0, base1 = li * sections, (li + 1) * sections
        for i in range(sections):
            j = (i + 1) % sections
            a, b, c, d = base0 + i, base0 + j, base1 + i, base1 + j
            faces.append([a, b, d])
            faces.append([a, d, c])
    faces = np.array(faces)

    bottom_v, bottom_f = _triangulate_cap(rings[0], zs[0], flip=True)
    off_b = len(verts)
    verts = np.vstack([verts, bottom_v])
    bottom_f = bottom_f + off_b

    top_v, top_f = _triangulate_cap(rings[-1], zs[-1], flip=False)
    off_t = len(verts)
    verts = np.vstack([verts, top_v])
    top_f = top_f + off_t

    all_faces = np.vstack([faces, bottom_f, top_f])
    mesh = trimesh.Trimesh(vertices=verts, faces=all_faces, process=True)
    if mesh.volume < 0:
        mesh.invert()  # normals pekade inåt (kan hända beroende på punktordning i profilerna)
    return mesh


# --------------------------------------------------------------------------- #
# 1) FRI 2D-KONTUR, EXTRUDERAD (med valfri avsmalning/vridning)
# --------------------------------------------------------------------------- #

class ExtrudeProfileInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    points: list = Field(
        description=(
            "Lista av [x_mm, y_mm]-punkter som definierar en sluten, GODTYCKLIG "
            "2D-kontur (stjärna, logotyp, oregelbunden panel, vad som helst). "
            "Minst 3 punkter. Behöver inte upprepa startpunkten."
        )
    )
    height_mm: float = Field(description="Extruderingshöjd (mm)")
    taper_ratio: float = Field(
        default=1.0,
        description="Skala på toppkonturen relativt botten. 1.0=rak, 0.5=halveras mot toppen, 0=spets.",
    )
    twist_degrees: float = Field(default=0.0, description="Vridning av toppkonturen relativt botten, grader.")
    sections: int = Field(default=48, description="Upplösning runt konturen (högre = mjukare kurvor, långsammare)")
    material: Optional[str] = Field(default=None, description="Materialnyckel för massberäkning, se list_materials")
    export_format: str = Field(default="stl", description="'stl', 'obj' eller '3mf'")


@tool("extrude_custom_profile", args_schema=ExtrudeProfileInput)
def extrude_custom_profile(name: str, points: list, height_mm: float,
                            taper_ratio: float = 1.0, twist_degrees: float = 0.0,
                            sections: int = 48, material: Optional[str] = None,
                            export_format: str = "stl") -> str:
    """Extrudera en HELT FRI 2D-kontur (godtycklig lista av punkter — stjärnor,
    pilar, logotyper, oregelbundna paneler, vad som helst) till en 3D-form,
    med valfri avsmalning (taper_ratio) och vridning (twist_degrees) mellan
    botten och topp. Detta är det mest generella verktyget för att skapa en
    form som inte är en enkel box/cylinder/sphere/cone — använd det när
    formen har en unik, icke-rektangulär kontur sedd uppifrån."""
    if len(points) < 3:
        return "points måste innehålla minst 3 punkter."
    if height_mm <= 0:
        return "height_mm måste vara > 0."

    bottom = np.array(points, dtype=float)
    top = bottom * taper_ratio
    if twist_degrees:
        theta = math.radians(twist_degrees)
        rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        top = top @ rot.T

    try:
        mesh = _loft_mesh([(bottom.tolist(), 0.0), (top.tolist(), height_mm)], sections=sections)
    except Exception as e:
        return f"Kunde inte skapa formen: {e}"

    try:
        fpath = _export(mesh, name, export_format)
    except ValueError as e:
        return str(e)

    report = {
        "file_path": str(fpath),
        "height_mm": height_mm,
        "taper_ratio": taper_ratio,
        "twist_degrees": twist_degrees,
        "num_profile_points": len(points),
    }
    report.update(_geometry_report(mesh, material_key=material, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 2) SVARVAD FORM (revolve) — organiska rundade former: vaser, flaskor,
#    handtag, fötter, runda knoppar
# --------------------------------------------------------------------------- #

class RevolveProfileInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    profile_points: list = Field(
        description=(
            "Lista av [radius_mm, z_mm]-punkter, sedda som ett tvärsnitt från "
            "mittaxeln och uppåt (som en 'silhuett' av halva föremålet). "
            "T.ex. en vas: [[0,0],[15,5],[18,15],[12,25],[14,35],[8,40],[0,45]]. "
            "radius=0 i första/sista punkten ger en helt sluten, solid form."
        )
    )
    sections: int = Field(default=64, description="Antal segment runt axeln (högre = rundare)")
    wall_thickness_mm: Optional[float] = Field(
        default=None,
        description=(
            "Om satt: skapar en ihålig version (t.ex. en riktig vas/mugg) med "
            "denna väggtjocklek istället för en solid form."
        ),
    )
    open_top: bool = Field(default=True, description="Om ihålig: öppen topp (mugg/vas) eller sluten (flaska).")
    material: Optional[str] = Field(default=None, description="Materialnyckel, se list_materials")
    export_format: str = Field(default="stl", description="'stl', 'obj' eller '3mf'")


@tool("revolve_profile", args_schema=RevolveProfileInput)
def revolve_profile(name: str, profile_points: list, sections: int = 64,
                     wall_thickness_mm: Optional[float] = None, open_top: bool = True,
                     material: Optional[str] = None, export_format: str = "stl") -> str:
    """Skapa en 'svarvad' organisk form genom att rotera en radie-profil 360°
    runt Z-axeln — samma princip som en verklig svarv eller en drejskiva.
    Perfekt för vaser, flaskor, handtag, runda fötter/knoppar och andra
    former som är symmetriska runt en axel men har en fri, kurvig silhuett.
    Ange wall_thickness_mm för en riktig ihålig behållare istället för en
    solid form."""
    profile = np.array(profile_points, dtype=float)
    if len(profile) < 2:
        return "profile_points måste innehålla minst 2 punkter."

    try:
        outer = trimesh.creation.revolve(profile, sections=sections, cap=True)
    except Exception as e:
        return f"Kunde inte skapa formen (kontrollera att profilen är giltig): {e}"

    if wall_thickness_mm:
        inner_profile = profile.copy()
        inner_profile[:, 0] = np.clip(inner_profile[:, 0] - wall_thickness_mm, 0, None)
        z_min, z_max = profile[:, 1].min(), profile[:, 1].max()
        if open_top:
            # ta bort toppens "lock" i inner-profilen genom att förlänga uppåt
            inner_profile = np.vstack([inner_profile, [inner_profile[-1, 0], z_max + wall_thickness_mm * 4]])
        try:
            inner = trimesh.creation.revolve(inner_profile, sections=sections, cap=not open_top)
            mesh = trimesh.boolean.difference([outer, inner])
        except Exception as e:
            return (f"Kunde inte urholka formen ({e}). Installera 'manifold3d' "
                    f"(pip install manifold3d) om det saknas.")
    else:
        mesh = outer

    try:
        fpath = _export(mesh, name, export_format)
    except ValueError as e:
        return str(e)

    report = {
        "file_path": str(fpath),
        "hollow": bool(wall_thickness_mm),
        "wall_thickness_mm": wall_thickness_mm,
        "sections": sections,
    }
    report.update(_geometry_report(mesh, material_key=material, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 3) LOFT MELLAN FLERA PROFILER — den mest generella formen av alla
# --------------------------------------------------------------------------- #

class LoftProfilesInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    profiles: list = Field(
        description=(
            "Lista av {'points': [[x_mm,y_mm],...], 'z_mm': float}, sorterad "
            "efter z_mm (botten till topp). Varje profil kan ha en HELT "
            "ANNAN form och antal punkter — t.ex. en stjärnformad botten som "
            "övergår i en cirkulär topp. Minst 2 profiler krävs."
        )
    )
    sections: int = Field(default=48, description="Upplösning runt konturen (högre = mjukare övergångar)")
    material: Optional[str] = Field(default=None, description="Materialnyckel, se list_materials")
    export_format: str = Field(default="stl", description="'stl', 'obj' eller '3mf'")


@tool("loft_profiles", args_schema=LoftProfilesInput)
def loft_profiles(name: str, profiles: list, sections: int = 48,
                   material: Optional[str] = None, export_format: str = "stl") -> str:
    """Skapa den mest generella typen av komplex form: lofta (mjukt övergå)
    mellan valfritt antal olika 2D-profiler placerade vid olika höjder.
    Profilerna kan vara helt olika former (t.ex. en fyrkant som övergår i en
    stjärna som övergår i en cirkel) — använd detta för organiska,
    skulpturala eller kraftigt formförändrande geometrier som varken
    extrude_custom_profile (rak/enkel taper) eller revolve_profile
    (axialsymmetrisk) klarar."""
    if len(profiles) < 2:
        return "profiles måste innehålla minst 2 profiler (botten + topp, eller fler mellansteg)."

    parsed = []
    for p in profiles:
        pts = p.get("points")
        z = p.get("z_mm")
        if not pts or z is None:
            return "Varje profil måste ha 'points' och 'z_mm'."
        parsed.append((pts, float(z)))
    parsed.sort(key=lambda t: t[1])

    try:
        mesh = _loft_mesh(parsed, sections=sections)
    except Exception as e:
        return f"Kunde inte lofta profilerna: {e}"

    try:
        fpath = _export(mesh, name, export_format)
    except ValueError as e:
        return str(e)

    report = {
        "file_path": str(fpath),
        "num_profiles": len(parsed),
        "z_range_mm": [parsed[0][1], parsed[-1][1]],
    }
    report.update(_geometry_report(mesh, material_key=material, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 4) UTJÄMNING — runda av skarpa CSG-kanter till organisk yta
# --------------------------------------------------------------------------- #

class SmoothMeshInput(BaseModel):
    file_path: str = Field(description="Sökväg till existerande STL/OBJ/3MF-fil")
    iterations: int = Field(default=5, description="Antal utjämningsiterationer (fler = mjukare, men krymper formen mer)")
    subdivide_passes: int = Field(
        default=3,
        description=(
            "Antal gånger varje triangel delas i 4 innan utjämning. LÅGUPPLÖSTA "
            "CSG-former (t.ex. en enkel box med 12 trianglar) MÅSTE ha minst "
            "2-3 passes, annars kollapsar utjämningen formens volym drastiskt "
            "istället för att bara runda kanterna (för få vertices för Taubin-"
            "filtret att ha något att jämna ut mot)."
        ),
    )
    name: Optional[str] = Field(default=None, description="Filnamn för resultatet (annars skrivs originalet över)")
    export_format: str = Field(default="stl", description="'stl', 'obj' eller '3mf'")


@tool("smooth_mesh", args_schema=SmoothMeshInput)
def smooth_mesh(file_path: str, iterations: int = 5, subdivide_passes: int = 3,
                 name: Optional[str] = None, export_format: str = "stl") -> str:
    """Runda av en modells skarpa kanter med Taubin-utjämning, så en hård
    CSG-form (box/cylinder-baserad) börjar likna en mjuk, organisk skulpterad
    yta. Funkar på VILKEN modell som helst — resultat från create_3d_model,
    combine_3d_models, compose_shapes, extrude_custom_profile, m.fl. Bra sista
    steg för organiska former; kör INTE detta på delar som behöver exakta
    plana ytor/hål för montering (skruvhål blir något ovala). Om resultatets
    volym krympt mycket jämfört med originalet, kör igen med fler
    subdivide_passes eller färre iterations."""
    mesh = trimesh.load(file_path, force="mesh")
    original_volume = float(mesh.volume)
    for _ in range(max(subdivide_passes, 0)):
        mesh = mesh.subdivide()
    try:
        mesh = trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=iterations)
    except Exception as e:
        return f"Utjämning misslyckades: {e}"

    volume_retained_pct = round(100 * float(mesh.volume) / original_volume, 1) if original_volume else None

    try:
        fpath = _export(mesh, name or Path(file_path).stem, export_format)
    except ValueError as e:
        return str(e)

    report = {
        "file_path": str(fpath),
        "iterations": iterations,
        "subdivide_passes": subdivide_passes,
        "volume_retained_pct": volume_retained_pct,
    }
    report.update(_geometry_report(mesh, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 5) BATCH-KOMPOSITION — bygg en hel sammansatt del i ETT anrop
# --------------------------------------------------------------------------- #

_PRIMITIVE_BUILDERS = {
    "box": lambda d: trimesh.creation.box(extents=[d["x_mm"], d["y_mm"], d["z_mm"]]),
    "cylinder": lambda d: trimesh.creation.cylinder(radius=d["radius_mm"], height=d["height_mm"], sections=int(d.get("sections", 32))),
    "sphere": lambda d: trimesh.creation.icosphere(radius=d["radius_mm"]),
    "cone": lambda d: trimesh.creation.cone(radius=d["radius_mm"], height=d["height_mm"], sections=int(d.get("sections", 32))),
}


def _apply_transform(mesh: "trimesh.Trimesh", op: dict) -> "trimesh.Trimesh":
    scale = op.get("scale")
    if scale is not None:
        if isinstance(scale, (int, float)):
            mesh.apply_scale(scale)
        else:
            mesh.apply_transform(trimesh.transformations.scale_matrix(1.0, [0, 0, 0]) if False else np.diag(list(scale) + [1.0]))
    rotate = op.get("rotate_deg")
    if rotate:
        rx, ry, rz = [math.radians(a) for a in rotate]
        if rx:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(rx, [1, 0, 0]))
        if ry:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(ry, [0, 1, 0]))
        if rz:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(rz, [0, 0, 1]))
    translate = op.get("translate")
    if translate:
        mesh.apply_translation(translate)
    return mesh


class ComposeShapesInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse) för slutresultatet")
    ops: list = Field(
        description=(
            "Lista av byggsteg, applicerade i ordning. Varje steg är ett dict:\n"
            "{'shape': 'box'|'cylinder'|'sphere'|'cone'|'file',\n"
            " 'dimensions': {...} (för primitiver, mm — box: x_mm/y_mm/z_mm, "
            "cylinder/cone: radius_mm/height_mm, sphere: radius_mm) ELLER "
            "'file_path': '...' (för shape='file', laddar en befintlig STL/OBJ),\n"
            " 'translate': [x,y,z] (valfri, mm),\n"
            " 'rotate_deg': [x,y,z] (valfri, grader),\n"
            " 'scale': tal eller [x,y,z] (valfri),\n"
            " 'operation': 'union'|'difference'|'intersection' (ignoreras för "
            "första steget, som blir startformen)}.\n"
            "Exempel: bas-box, subtrahera en cylinder (hål), lägg till en sfär "
            "(knopp ovanpå) — allt i EN körning istället för flera "
            "combine_3d_models-anrop med mellanliggande filer."
        )
    )
    material: Optional[str] = Field(default=None, description="Materialnyckel, se list_materials")
    export_format: str = Field(default="stl", description="'stl', 'obj' eller '3mf'")


@tool("compose_shapes", args_schema=ComposeShapesInput)
def compose_shapes(name: str, ops: list, material: Optional[str] = None,
                    export_format: str = "stl") -> str:
    """Bygg en hel sammansatt, komplex del i ETT anrop genom att kedja flera
    primitiver (box/cylinder/sphere/cone) eller befintliga filer med
    booleska operationer (union/difference/intersection) och egna
    transformer (translate/rotate/scale) för varje steg. Använd detta istället
    för att anropa create_3d_model + combine_3d_models flera gånger i rad när
    en form har flera detaljer (t.ex. en platta med flera hål OCH en
    pelare OCH en avfasad kant) — snabbare och färre mellanliggande filer.
    Kräver 'manifold3d' för de booleska operationerna."""
    if not ops:
        return "ops får inte vara tom."

    result = None
    for i, op in enumerate(ops):
        shape = op.get("shape")
        if shape == "file":
            mesh = trimesh.load(op["file_path"], force="mesh")
        elif shape in _PRIMITIVE_BUILDERS:
            try:
                mesh = _PRIMITIVE_BUILDERS[shape](op.get("dimensions", {}))
            except KeyError as e:
                return f"Steg {i}: saknar dimension {e} för shape='{shape}'."
        else:
            return f"Steg {i}: okänd shape '{shape}'. Använd box, cylinder, sphere, cone eller file."

        mesh = _apply_transform(mesh, op)

        if result is None:
            result = mesh
            continue

        operation = op.get("operation", "union")
        try:
            if operation == "union":
                result = trimesh.boolean.union([result, mesh])
            elif operation == "difference":
                result = trimesh.boolean.difference([result, mesh])
            elif operation == "intersection":
                result = trimesh.boolean.intersection([result, mesh])
            else:
                return f"Steg {i}: operation måste vara union, difference eller intersection."
        except Exception as e:
            return f"Steg {i}: boolesk operation misslyckades ({e}). Kontrollera att 'manifold3d' är installerat."

    try:
        fpath = _export(result, name, export_format)
    except ValueError as e:
        return str(e)

    report = {"file_path": str(fpath), "num_steps": len(ops)}
    report.update(_geometry_report(result, material_key=material, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Registrering
# --------------------------------------------------------------------------- #

def get_complex_tools() -> list:
    """Returnerar de nya verktygen, redo att läggas till i model3d_tools.get_tools()."""
    return [
        extrude_custom_profile,
        revolve_profile,
        loft_profiles,
        smooth_mesh,
        compose_shapes,
    ]


if __name__ == "__main__":
    # Stjärnbotten -> cirkulär topp (kräver genuint loft, inte bara taper)
    theta = [i * 36 for i in range(10)]
    star = [[(20 if i % 2 == 0 else 10) * math.cos(math.radians(a)),
             (20 if i % 2 == 0 else 10) * math.sin(math.radians(a))]
            for i, a in enumerate(theta)]
    circle = [[15 * math.cos(math.radians(a)), 15 * math.sin(math.radians(a))]
              for a in range(0, 360, 12)]
    print(loft_profiles.invoke({
        "name": "selftest_star_to_circle",
        "profiles": [{"points": star, "z_mm": 0}, {"points": circle, "z_mm": 40}],
    }))

    print(revolve_profile.invoke({
        "name": "selftest_vase",
        "profile_points": [[0, 0], [15, 5], [18, 15], [12, 25], [14, 35], [8, 40], [0, 45]],
        "wall_thickness_mm": 2.0,
    }))

    print(compose_shapes.invoke({
        "name": "selftest_bracket_with_holes",
        "ops": [
            {"shape": "box", "dimensions": {"x_mm": 40, "y_mm": 40, "z_mm": 10}},
            {"shape": "cylinder", "dimensions": {"radius_mm": 4, "height_mm": 40}, "operation": "difference"},
            {"shape": "sphere", "dimensions": {"radius_mm": 6}, "translate": [0, 0, 5], "operation": "union"},
        ],
    }))
