"""
model3d_tools.py
=================
LangChain-verktyg som ger en AI-agent förmåga att:
  1. Skapa 3D-modeller (primitiver + booleska kombinationer) och exportera STL/OBJ
  2. Testa modellernas hållfasthet ("tålighet") under last, med olika material
  3. Simulera olika miljöer (temperatur, fukt, korrosion) som påverkar materialets
     effektiva hållfasthet
  4. Ladda ner / lägga till nya material i den lokala materialdatabasen (JSON),
     så nya material kan tillkomma i framtiden utan kodändring

Beroenden:
    pip install --break-system-packages langchain langchain-core trimesh numpy requests \
        matplotlib shapely rtree manifold3d

Designval / begränsningar:
    - Ingen tung FEA-motor (t.ex. FEniCS/CalculiX) används. Istället görs en
      "FEA-lite"-beräkning baserad på klassisk balkteori (Euler-Bernoulli) för
      spänning och nedböjning. Det ger snabba, deterministiska, "good enough"-
      resultat för konceptuell hållfasthetstestning utan tunga externa beroenden
      eller nätverksåtkomst till en riktig solver.
    - Materialdatabasen är en lokal JSON-fil som går att utöka via
      `download_material` (hämtar JSON från valfri URL) eller `add_material`
      (manuell inmatning). Detta gör "ladda ner nya material i framtiden" möjligt
      utan att hårdkoda en specifik extern tjänst.
    - Geometri hanteras med `trimesh`. Modeller sparas som riktiga filer på disk
      så att agenten (eller användaren) kan öppna dem i valfri 3D-mjukvara.

Användning i en LangChain-agent:
    from model3d_tools import get_tools
    tools = get_tools()  # lista med @tool-dekorerade funktioner
    agent = create_react_agent(llm, tools, ...)  # eller annan agent-typ
"""

from __future__ import annotations

import json
import math
import os
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
import trimesh
from langchain_core.tools import tool
from pydantic import BaseModel, Field

import matplotlib
matplotlib.use("Agg")  # headless, ingen display krävs
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from shapely.geometry import Point
from shapely.ops import unary_union

# --------------------------------------------------------------------------- #
# Konfiguration / lagringsplatser
# --------------------------------------------------------------------------- #

BASE_DIR = Path(os.environ.get("ai_workspace", Path.home() / ".model3d_tools"))
MODELS_DIR = BASE_DIR / "models"
RENDERS_DIR = BASE_DIR / "renders"
MATERIALS_FILE = BASE_DIR / "materials.json"
REFERENCE_SHAPES_FILE = BASE_DIR / "reference_shapes.json"

MODELS_DIR.mkdir(parents=True, exist_ok=True)
RENDERS_DIR.mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Materialdatabas
# --------------------------------------------------------------------------- #
# Enheter:
#   density            kg/m^3
#   young_modulus_pa   Pascal (N/m^2)
#   yield_strength_pa  Pascal (N/m^2)  -- gräns för plastisk deformation
#   poisson_ratio      dimensionslös

DEFAULT_MATERIALS = {
    "pla": {
        "density": 1250, "young_modulus_pa": 3.5e9,
        "yield_strength_pa": 50e6, "poisson_ratio": 0.36,
        "fatigue_limit_ratio": 0.30, "elongation_at_break_pct": 4,
        "notes": "Vanlig 3D-print (FDM), spröd vid låg temp.",
    },
    "abs": {
        "density": 1040, "young_modulus_pa": 2.3e9,
        "yield_strength_pa": 40e6, "poisson_ratio": 0.35,
        "fatigue_limit_ratio": 0.35, "elongation_at_break_pct": 25,
        "notes": "Segare än PLA, tål högre temperatur.",
    },
    "tpu_95a": {
        "density": 1210, "young_modulus_pa": 26e6,
        "yield_strength_pa": 35e6, "poisson_ratio": 0.45,
        "fatigue_limit_ratio": 0.45, "elongation_at_break_pct": 450,
        "notes": ("Flexibel elastomer (Shore 95A), typisk för stötdämpande skal/case. "
                   "'yield_strength' avser här draghållfasthet, inte klassisk flytgräns."),
    },
    "aluminum_6061": {
        "density": 2700, "young_modulus_pa": 69e9,
        "yield_strength_pa": 276e6, "poisson_ratio": 0.33,
        "fatigue_limit_ratio": 0.30, "elongation_at_break_pct": 12,
        "notes": "Vanlig konstruktionsaluminium.",
    },
    "steel_mild": {
        "density": 7850, "young_modulus_pa": 200e9,
        "yield_strength_pa": 250e6, "poisson_ratio": 0.29,
        "fatigue_limit_ratio": 0.50, "elongation_at_break_pct": 20,
        "notes": "Konstruktionsstål, AISI 1018-liknande.",
    },
    "titanium_ti6al4v": {
        "density": 4430, "young_modulus_pa": 113.8e9,
        "yield_strength_pa": 880e6, "poisson_ratio": 0.34,
        "fatigue_limit_ratio": 0.55, "elongation_at_break_pct": 14,
        "notes": "Flyg-/rymdkvalitet titanlegering.",
    },
    "wood_pine": {
        "density": 500, "young_modulus_pa": 9e9,
        "yield_strength_pa": 40e6, "poisson_ratio": 0.3,
        "fatigue_limit_ratio": 0.25, "elongation_at_break_pct": 2,
        "notes": "Ungefärliga värden, starkt riktningsberoende i verkligheten.",
    },
    "glass_soda_lime": {
        "density": 2500, "young_modulus_pa": 70e9,
        "yield_strength_pa": 33e6, "poisson_ratio": 0.22,
        "fatigue_limit_ratio": 0.15, "elongation_at_break_pct": 0.1,
        "notes": "Mycket spröd, låg draghållfasthet i praktiken.",
    },
    "concrete": {
        "density": 2400, "young_modulus_pa": 30e9,
        "yield_strength_pa": 3e6, "poisson_ratio": 0.2,
        "fatigue_limit_ratio": 0.20, "elongation_at_break_pct": 0.02,
        "notes": "Draghållfasthet (ej tryck), verklig tryckhållfasthet är mycket högre.",
    },
}


def _load_materials() -> dict:
    if not MATERIALS_FILE.exists():
        _save_materials(DEFAULT_MATERIALS)
        return dict(DEFAULT_MATERIALS)
    with open(MATERIALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_materials(mats: dict) -> None:
    with open(MATERIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(mats, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Miljöeffekter (enkla, transparenta modifierare)
# --------------------------------------------------------------------------- #
# Varje miljö ger en multiplikator på yield_strength och young_modulus.
# Detta är en förenklad approximation, inte labbdata.

ENVIRONMENTS = {
    "normal": {"yield_mult": 1.00, "modulus_mult": 1.00, "desc": "20°C, torrt, ingen korrosion."},
    "high_heat": {"yield_mult": 0.70, "modulus_mult": 0.85, "desc": "~150°C: material mjuknar."},
    "low_temp": {"yield_mult": 0.90, "modulus_mult": 1.05, "desc": "~-30°C: styvare men sprödare (ej modellerat)."},
    "humid": {"yield_mult": 0.95, "modulus_mult": 0.97, "desc": "Hög luftfuktighet, viss materialförsvagning."},
    "corrosive": {"yield_mult": 0.60, "modulus_mult": 0.90, "desc": "Salt/kemisk miljö, betydande försvagning över tid."},
    "underwater": {"yield_mult": 0.85, "modulus_mult": 0.95, "desc": "Nedsänkt i vatten, viss urlakning/korrosion."},
}


# --------------------------------------------------------------------------- #
# Referensmått-databas
# --------------------------------------------------------------------------- #
# Syfte: agenten ska kunna "hitta formen" på en existerande produkt (t.ex.
# Galaxy Buds3 Pro-fodralet) innan den designar ett skal/case runt den.
# Måtten är yttermått i millimeter (W x H x D), plus ev. övrig geometri-info.
# Databasen är, precis som materialdatabasen, utökningsbar via add/download.

DEFAULT_REFERENCE_SHAPES = {
    "galaxy_buds3_pro_case": {
        "category": "earbuds_case",
        "outer_width_mm": 58.9,
        "outer_height_mm": 48.7,
        "outer_depth_mm": 24.4,
        "shape": "rounded_rectangular_prism",
        "corner_radius_mm": 8.0,
        "notes": (
            "Yttermått från Samsungs officiella specifikation (WxHxD). "
            "corner_radius_mm är en uppskattning baserat på formspråket, "
            "inte ett officiellt mått – justera vid behov."
        ),
        "source": "samsung.com / devicesupport.three.co.uk",
    },
}


def _load_reference_shapes() -> dict:
    if not REFERENCE_SHAPES_FILE.exists():
        _save_reference_shapes(DEFAULT_REFERENCE_SHAPES)
        return dict(DEFAULT_REFERENCE_SHAPES)
    with open(REFERENCE_SHAPES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_reference_shapes(shapes: dict) -> None:
    with open(REFERENCE_SHAPES_FILE, "w", encoding="utf-8") as f:
        json.dump(shapes, f, indent=2, ensure_ascii=False)


class LookupReferenceInput(BaseModel):
    query: str = Field(default="", description="Fritextsökning i namn/kategori/notes, tomt = visa alla")


@tool("lookup_reference_shape", args_schema=LookupReferenceInput)
def lookup_reference_shape(query: str = "") -> str:
    """Slå upp kända produktmått i den lokala referensdatabasen (t.ex.
    'galaxy_buds3_pro_case'). Använd detta INNAN du designar ett skal/case,
    så innermåtten stämmer med det riktiga föremålet. Om produkten inte finns
    här: sök upp officiella mått på webben och lägg till dem med
    add_reference_shape eller download_reference_shape."""
    shapes = _load_reference_shapes()
    q = query.lower().strip()
    if not q:
        return json.dumps(shapes, indent=2, ensure_ascii=False)
    hits = {k: v for k, v in shapes.items()
            if q in k.lower() or q in json.dumps(v, ensure_ascii=False).lower()}
    if not hits:
        return (f"Ingen träff på '{query}'. Tillgängliga: {', '.join(shapes.keys())}. "
                f"Sök upp officiella mått på webben och lägg till med add_reference_shape.")
    return json.dumps(hits, indent=2, ensure_ascii=False)


class AddReferenceShapeInput(BaseModel):
    name: str = Field(description="Nyckel, t.ex. 'iphone_16_pro' eller 'galaxy_buds3_case'")
    category: str = Field(description="T.ex. 'earbuds_case', 'phone', 'controller'")
    outer_width_mm: float = Field(description="Yttermått bredd (mm)")
    outer_height_mm: float = Field(description="Yttermått höjd (mm)")
    outer_depth_mm: float = Field(description="Yttermått djup/tjocklek (mm)")
    shape: str = Field(default="rounded_rectangular_prism", description="Grundform")
    corner_radius_mm: float = Field(default=3.0, description="Uppskattad hörnradie (mm)")
    notes: str = Field(default="", description="Källa/anteckningar")


@tool("add_reference_shape", args_schema=AddReferenceShapeInput)
def add_reference_shape(name: str, category: str, outer_width_mm: float,
                         outer_height_mm: float, outer_depth_mm: float,
                         shape: str = "rounded_rectangular_prism",
                         corner_radius_mm: float = 3.0, notes: str = "") -> str:
    """Lägg till kända produktmått manuellt (t.ex. efter en websökning) i
    referensdatabasen, så de kan återanvändas för att designa skal/case."""
    shapes = _load_reference_shapes()
    shapes[name.lower().strip()] = {
        "category": category, "outer_width_mm": outer_width_mm,
        "outer_height_mm": outer_height_mm, "outer_depth_mm": outer_depth_mm,
        "shape": shape, "corner_radius_mm": corner_radius_mm, "notes": notes,
    }
    _save_reference_shapes(shapes)
    return f"Referensform '{name}' sparad i {REFERENCE_SHAPES_FILE}."


class DownloadReferenceShapeInput(BaseModel):
    url: str = Field(description="URL till JSON med ett eller flera referensmått, samma format som databasen")


@tool("download_reference_shape", args_schema=DownloadReferenceShapeInput)
def download_reference_shape(url: str) -> str:
    """Hämta referensmått för produkter från en URL (JSON) och lägg till dem
    lokalt, så nya produktformer kan tillkomma i framtiden."""
    import requests
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Kunde inte hämta/tolka JSON från {url}: {e}"

    shapes = _load_reference_shapes()
    added = []
    required = ("outer_width_mm", "outer_height_mm", "outer_depth_mm")

    def _valid(entry: dict) -> bool:
        return all(k in entry for k in required)

    if "name" in data and _valid(data):
        key = str(data["name"]).lower().strip()
        shapes[key] = {k: v for k, v in data.items() if k != "name"}
        added.append(key)
    else:
        for key, entry in data.items():
            if isinstance(entry, dict) and _valid(entry):
                shapes[key.lower().strip()] = entry
                added.append(key)

    if not added:
        return f"Ingen giltig referensdata hittades. Förväntade fält: {', '.join(required)}."

    _save_reference_shapes(shapes)
    return f"Lade till/uppdaterade {len(added)} referensformer: {', '.join(added)}."


# --------------------------------------------------------------------------- #
# Hjälpfunktion: utökad geometrirapport (används av flera skapar-verktyg)
# --------------------------------------------------------------------------- #

def _geometry_report(mesh: "trimesh.Trimesh", material_key: Optional[str] = None,
                      volume_unit_is_mm3: bool = False) -> dict:
    """Bygger en rejäl geometrirapport: mått, massa, tröghetsmoment,
    konvexitet, m.m. Om material_key ges räknas massa och (för mm-skalade
    modeller) ungefärlig 3D-printtid ut."""
    try:
        convex_ratio = float(mesh.volume) / float(mesh.convex_hull.volume) if mesh.convex_hull.volume else None
    except Exception:
        convex_ratio = None

    report = {
        "volume": round(float(mesh.volume), 6),
        "volume_unit": "mm^3" if volume_unit_is_mm3 else "m^3",
        "surface_area": round(float(mesh.area), 6),
        "surface_area_unit": "mm^2" if volume_unit_is_mm3 else "m^2",
        "bounding_box": [round(float(v), 4) for v in mesh.extents.tolist()],
        "bounding_sphere_radius": round(float(mesh.bounding_sphere.primitive.radius), 4),
        "center_of_mass": [round(float(v), 5) for v in mesh.center_mass],
        "watertight": bool(mesh.is_watertight),
        "euler_number": int(mesh.euler_number),
        "is_convex": bool(mesh.is_convex),
        "solidity_ratio": round(convex_ratio, 4) if convex_ratio else None,
        "solidity_note": (
            "solidity_ratio nära 1.0 = kompakt/konvex form. Lågt värde = mycket "
            "urholkad/utstickande geometri (mer detaljrik form)."
        ),
        "moment_of_inertia_diagonal": [round(float(v), 6) for v in np.diag(mesh.moment_inertia)],
    }

    if material_key:
        mats = _load_materials()
        mk = material_key.lower().strip()
        if mk in mats:
            density = mats[mk]["density"]  # kg/m^3
            if volume_unit_is_mm3:
                volume_m3 = float(mesh.volume) * 1e-9
            else:
                volume_m3 = float(mesh.volume)
            mass_kg = density * volume_m3
            report["material"] = mk
            report["estimated_mass_g"] = round(mass_kg * 1000, 2)

            if volume_unit_is_mm3:
                # Grov FDM-printtidsuppskattning: ~12 mm^3/s extruderingshastighet
                # vid typiska hobby-inställningar (0.4mm munstycke, medelfart).
                extrusion_rate_mm3_s = 12.0
                print_seconds = float(mesh.volume) / extrusion_rate_mm3_s
                report["estimated_print_time_min"] = round(print_seconds / 60, 1)
                report["print_time_note"] = (
                    "Mycket grov uppskattning baserad på volym/extruderingshastighet "
                    "(~12 mm³/s). Verklig tid beror på skalvägg, infill, hastighet m.m."
                )
        else:
            report["material_warning"] = f"Material '{material_key}' finns inte i databasen, massa ej beräknad."

    return report


# --------------------------------------------------------------------------- #
# 1) SKAPA 3D-MODELLER
# --------------------------------------------------------------------------- #

class CreateModelInput(BaseModel):
    shape: str = Field(description="Form: 'box', 'cylinder', 'sphere', eller 'cone'")
    dimensions: dict = Field(
        description=(
            "Mått i meter. box: {'x','y','z'}. cylinder/cone: {'radius','height'}. "
            "sphere: {'radius'}."
        )
    )
    name: Optional[str] = Field(default=None, description="Valfritt filnamn (utan filändelse)")
    export_format: str = Field(default="stl", description="'stl' eller 'obj'")
    material: Optional[str] = Field(
        default=None,
        description="Valfri materialnyckel (se list_materials) för att räkna ut massa direkt.",
    )


@tool("create_3d_model", args_schema=CreateModelInput)
def create_3d_model(shape: str, dimensions: dict, name: Optional[str] = None,
                     export_format: str = "stl", material: Optional[str] = None) -> str:
    """Skapa en enkel 3D-modell (box, cylinder, sphere, cone) och spara den som
    STL eller OBJ. Returnerar en utförlig geometrirapport (volym, yta,
    bounding box, tyngdpunkt, tröghetsmoment, konvexitet, ev. massa om
    material anges) som kan användas direkt i vidare hållfasthetstester."""
    shape = shape.lower().strip()
    d = dimensions

    if shape == "box":
        mesh = trimesh.creation.box(extents=[d["x"], d["y"], d["z"]])
    elif shape == "cylinder":
        mesh = trimesh.creation.cylinder(radius=d["radius"], height=d["height"])
    elif shape == "sphere":
        mesh = trimesh.creation.icosphere(radius=d["radius"])
    elif shape == "cone":
        mesh = trimesh.creation.cone(radius=d["radius"], height=d["height"])
    else:
        return f"Okänd form '{shape}'. Använd box, cylinder, sphere eller cone."

    if export_format not in ("stl", "obj"):
        return "export_format måste vara 'stl' eller 'obj'."

    fname = f"{name or (shape + '_' + uuid.uuid4().hex[:8])}.{export_format}"
    fpath = MODELS_DIR / fname
    mesh.export(fpath)

    report = {"file_path": str(fpath), "shape": shape}
    report.update(_geometry_report(mesh, material_key=material, volume_unit_is_mm3=False))
    return json.dumps(report, ensure_ascii=False)


class CombineModelsInput(BaseModel):
    file_path_a: str = Field(description="Sökväg till första modellen (STL/OBJ)")
    file_path_b: str = Field(description="Sökväg till andra modellen (STL/OBJ)")
    operation: str = Field(description="'union', 'difference' eller 'intersection'")
    name: Optional[str] = Field(default=None, description="Valfritt filnamn för resultatet")


@tool("combine_3d_models", args_schema=CombineModelsInput)
def combine_3d_models(file_path_a: str, file_path_b: str, operation: str,
                       name: Optional[str] = None) -> str:
    """Kombinera två existerande 3D-modeller med en boolesk operation
    (union/difference/intersection). Kräver att 'blender' eller 'manifold3d'
    finns installerat som bakomliggande boolean-motor för trimesh; annars
    returneras ett tydligt felmeddelande."""
    a = trimesh.load(file_path_a, force="mesh")
    b = trimesh.load(file_path_b, force="mesh")

    try:
        if operation == "union":
            result = trimesh.boolean.union([a, b])
        elif operation == "difference":
            result = trimesh.boolean.difference([a, b])
        elif operation == "intersection":
            result = trimesh.boolean.intersection([a, b])
        else:
            return "operation måste vara union, difference eller intersection."
    except Exception as e:
        return (f"Boolesk operation misslyckades ({e}). Installera t.ex. "
                f"'manifold3d' (pip install manifold3d) för att aktivera detta.")

    fname = f"{name or ('combined_' + uuid.uuid4().hex[:8])}.stl"
    fpath = MODELS_DIR / fname
    result.export(fpath)
    return json.dumps({"file_path": str(fpath), "volume_m3": round(float(result.volume), 6)},
                       ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 2) MATERIALDATABAS: lista / hämta / lägg till / ladda ner
# --------------------------------------------------------------------------- #

@tool("list_materials")
def list_materials() -> str:
    """Lista alla material som finns i den lokala materialdatabasen, med
    grundläggande egenskaper (densitet, E-modul, sträckgräns)."""
    mats = _load_materials()
    return json.dumps(mats, indent=2, ensure_ascii=False)


class AddMaterialInput(BaseModel):
    name: str = Field(description="Materialets namn/nyckel, t.ex. 'carbon_fiber'")
    density: float = Field(description="Densitet i kg/m^3")
    young_modulus_pa: float = Field(description="Elasticitetsmodul (E) i Pascal")
    yield_strength_pa: float = Field(description="Sträckgräns i Pascal")
    poisson_ratio: float = Field(default=0.3, description="Poissons tal")
    notes: str = Field(default="", description="Fritext-anteckning om materialet")


@tool("add_material", args_schema=AddMaterialInput)
def add_material(name: str, density: float, young_modulus_pa: float,
                  yield_strength_pa: float, poisson_ratio: float = 0.3,
                  notes: str = "") -> str:
    """Lägg till (eller uppdatera) ett material manuellt i den lokala
    materialdatabasen, så det kan användas i framtida hållfasthetstester."""
    mats = _load_materials()
    mats[name.lower().strip()] = {
        "density": density, "young_modulus_pa": young_modulus_pa,
        "yield_strength_pa": yield_strength_pa, "poisson_ratio": poisson_ratio,
        "notes": notes,
    }
    _save_materials(mats)
    return f"Material '{name}' sparat i {MATERIALS_FILE}."


class DownloadMaterialInput(BaseModel):
    url: str = Field(
        description=(
            "URL till en JSON-resurs. Förväntat format: antingen ett enda "
            "materialobjekt {'name':..., 'density':..., 'young_modulus_pa':..., "
            "'yield_strength_pa':...} eller en dict av flera material på samma form "
            "som materialdatabasen."
        )
    )


@tool("download_material", args_schema=DownloadMaterialInput)
def download_material(url: str) -> str:
    """Hämta nytt materialdata från en URL (JSON) och lägg till det i den
    lokala materialdatabasen. Används för att utöka materialbiblioteket i
    framtiden utan att koda om verktyget."""
    import requests
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Kunde inte hämta/tolka JSON från {url}: {e}"

    mats = _load_materials()
    added = []

    def _valid(entry: dict) -> bool:
        return all(k in entry for k in ("density", "young_modulus_pa", "yield_strength_pa"))

    if "name" in data and _valid(data):
        key = str(data["name"]).lower().strip()
        mats[key] = {k: v for k, v in data.items() if k != "name"}
        added.append(key)
    else:
        for key, entry in data.items():
            if isinstance(entry, dict) and _valid(entry):
                mats[key.lower().strip()] = entry
                added.append(key)

    if not added:
        return ("Ingen giltig materialdata hittades i svaret. Förväntade fält: "
                "density, young_modulus_pa, yield_strength_pa.")

    _save_materials(mats)
    return f"Lade till/uppdaterade {len(added)} material: {', '.join(added)}."


# --------------------------------------------------------------------------- #
# 3) HÅLLFASTHETSTEST ("tålighet")
# --------------------------------------------------------------------------- #
# Modell: rektangulär eller cylindrisk balk, fast inspänd i ena änden
# (cantilever), belastad med en punktlast F i fria änden. Klassisk
# Euler-Bernoulli-balkteori:
#
#   Böjspänning:   sigma = M * c / I
#   Nedböjning:    delta = F * L^3 / (3 * E * I)
#
# där M = F * L (max böjmoment vid infästning), c = avstånd till yttersta
# fiber, I = tröghetsmoment för tvärsnittet, E = elasticitetsmodul.
#
# Detta är en *förenklad* ingenjörsapproximation (inte en fullständig FEA),
# men ger ett vettigt, spårbart svar på "håller det här eller inte?" för
# enkla geometrier och laster.

class DurabilityTestInput(BaseModel):
    material: str = Field(description="Materialnyckel från materialdatabasen, t.ex. 'aluminum_6061'")
    cross_section: str = Field(description="'rectangular' eller 'circular'")
    dimensions: dict = Field(
        description=(
            "rectangular: {'width_m','height_m'} (height = böjriktning). "
            "circular: {'radius_m'}."
        )
    )
    length_m: float = Field(description="Balkens/modellens fria längd i meter (cantilever)")
    load_n: float = Field(description="Punktlast i Newton, applicerad i fria änden, vinkelrät mot balken")
    environment: str = Field(
        default="normal",
        description=f"En av: {', '.join(ENVIRONMENTS.keys())}",
    )


@tool("test_durability", args_schema=DurabilityTestInput)
def test_durability(material: str, cross_section: str, dimensions: dict,
                     length_m: float, load_n: float, environment: str = "normal") -> str:
    """Testa om en enkel balk-/stångformad modell håller för en given last,
    givet material och miljö. Ger en utförlig rapport: böjspänning,
    skjuvspänning, von Mises-ekvivalent spänning, säkerhetsfaktor,
    nedböjning, balkens egenvikt, samt en grov uppskattning av första
    böjsvängningens frekvens (Euler-Bernoulli balkteori, cantilever med
    punktlast i fri ände)."""
    mats = _load_materials()
    mat_key = material.lower().strip()
    if mat_key not in mats:
        return (f"Material '{material}' finns inte i databasen. Använd list_materials "
                f"för att se tillgängliga material, eller add_material/download_material "
                f"för att lägga till det.")
    mat = mats[mat_key]

    env_key = environment.lower().strip()
    if env_key not in ENVIRONMENTS:
        return f"Okänd miljö '{environment}'. Giltiga: {', '.join(ENVIRONMENTS.keys())}"
    env = ENVIRONMENTS[env_key]

    E = mat["young_modulus_pa"] * env["modulus_mult"]
    yield_strength = mat["yield_strength_pa"] * env["yield_mult"]
    density = mat["density"]
    fatigue_ratio = mat.get("fatigue_limit_ratio", 0.35)

    cross_section = cross_section.lower().strip()
    if cross_section == "rectangular":
        w = dimensions["width_m"]
        h = dimensions["height_m"]
        I = (w * h ** 3) / 12.0
        c = h / 2.0
        A = w * h
        # Max skjuvspänning i rektangulärt tvärsnitt: tau = 1.5 * V / A
        shear_factor = 1.5
    elif cross_section == "circular":
        r = dimensions["radius_m"]
        I = (math.pi * r ** 4) / 4.0
        c = r
        A = math.pi * r ** 2
        # Max skjuvspänning i cirkulärt tvärsnitt: tau = 4/3 * V / A
        shear_factor = 4.0 / 3.0
    else:
        return "cross_section måste vara 'rectangular' eller 'circular'."

    if I <= 0 or A <= 0:
        return "Ogiltiga dimensioner (tröghetsmoment eller tvärsnittsarea blev 0 eller negativt)."

    V = load_n                          # tvärkraft (konstant längs balken) [N]
    M = load_n * length_m               # max böjmoment vid infästning [N*m]
    sigma_bending = M * c / I           # böjspänning [Pa]
    tau_shear = shear_factor * V / A    # max skjuvspänning [Pa]
    # Von Mises för kombinerad böjning + skjuvning i en punkt (förenklat, 2D-spänningstillstånd)
    sigma_von_mises = math.sqrt(sigma_bending ** 2 + 3 * tau_shear ** 2)

    deflection = (load_n * length_m ** 3) / (3 * E * I)  # [m]
    safety_factor = yield_strength / sigma_von_mises if sigma_von_mises > 0 else float("inf")

    beam_mass_kg = density * A * length_m
    beam_volume_m3 = A * length_m

    # Första böjsvängningsfrekvensen för en cantilever med jämnt fördelad massa
    # (klassisk formel: f1 = (1.875^2 / (2*pi*L^2)) * sqrt(E*I / (rho*A)))
    try:
        natural_freq_hz = (1.875 ** 2 / (2 * math.pi * length_m ** 2)) * math.sqrt(E * I / (density * A))
    except (ValueError, ZeroDivisionError):
        natural_freq_hz = None

    # Utmattningsgräns (Basquin-approximation): spänning under vilken materialet
    # tål "oändligt" antal cykler (förenklat, ingen S-N-kurva-integration)
    fatigue_limit_pa = yield_strength * fatigue_ratio
    fatigue_safety_factor = fatigue_limit_pa / sigma_von_mises if sigma_von_mises > 0 else float("inf")

    if safety_factor >= 2.0:
        verdict = "HÅLLER GOTT (säkerhetsfaktor >= 2.0)"
    elif safety_factor >= 1.0:
        verdict = "HÅLLER MARGINELLT (säkerhetsfaktor mellan 1.0 och 2.0)"
    else:
        verdict = "GÅR SÖNDER (spänningen överstiger sträckgränsen)"

    if fatigue_safety_factor >= 1.5:
        fatigue_verdict = "Tål sannolikt upprepad/cyklisk belastning (t.ex. vibrationer, återkommande stötar)."
    elif fatigue_safety_factor >= 1.0:
        fatigue_verdict = "Marginellt mot utmattning — kan spricka efter många belastningscykler."
    else:
        fatigue_verdict = "Risk för utmattningsbrott vid upprepad belastning, även om enstaka belastning håller."

    return json.dumps({
        "material": mat_key,
        "environment": env_key,
        "environment_note": env["desc"],
        "geometry": {
            "cross_section_area_mm2": round(A * 1e6, 3),
            "beam_volume_cm3": round(beam_volume_m3 * 1e6, 3),
            "beam_mass_g": round(beam_mass_kg * 1000, 2),
        },
        "stress_analysis": {
            "max_bending_stress_mpa": round(sigma_bending / 1e6, 3),
            "max_shear_stress_mpa": round(tau_shear / 1e6, 3),
            "von_mises_equivalent_stress_mpa": round(sigma_von_mises / 1e6, 3),
            "yield_strength_used_mpa": round(yield_strength / 1e6, 3),
            "safety_factor": round(safety_factor, 3),
        },
        "deflection": {
            "max_deflection_mm": round(deflection * 1000, 4),
            "deflection_to_length_ratio": f"1:{round(length_m / deflection)}" if deflection > 0 else "n/a",
        },
        "vibration": {
            "estimated_first_natural_frequency_hz": round(natural_freq_hz, 2) if natural_freq_hz else None,
            "note": "Undvik driftvibrationer nära denna frekvens (resonansrisk).",
        },
        "fatigue_estimate": {
            "fatigue_limit_mpa": round(fatigue_limit_pa / 1e6, 3),
            "fatigue_safety_factor": round(fatigue_safety_factor, 3),
            "verdict": fatigue_verdict,
            "note": ("Mycket grov Basquin-approximation (fatigue_limit_ratio * sträckgräns). "
                     "Verklig utmattningslivslängd kräver S-N-data och cykelräkning."),
        },
        "verdict": verdict,
        "model_note": (
            "Förenklad Euler-Bernoulli-balkberäkning (cantilever, punktlast i fri ände). "
            "Inte en fullständig FEA - använd som konceptuell indikation, inte "
            "certifieringsunderlag."
        ),
    }, ensure_ascii=False)


class DropTestInput(BaseModel):
    material: str = Field(description="Materialnyckel, t.ex. 'tpu_95a' eller 'aluminum_6061'")
    cross_section: str = Field(description="'rectangular' eller 'circular'")
    dimensions: dict = Field(description="Se test_durability för format")
    length_m: float = Field(description="Fri längd/spännvidd i meter")
    drop_height_m: float = Field(description="Falhöjd i meter, t.ex. 1.0 för höftfickehöjd, 1.5 för fickhöjd")
    dropped_mass_kg: float = Field(description="Massan hos det fallande föremålet (t.ex. skal + hörlurar), kg")
    environment: str = Field(default="normal", description=f"En av: {', '.join(ENVIRONMENTS.keys())}")


@tool("simulate_drop_test", args_schema=DropTestInput)
def simulate_drop_test(material: str, cross_section: str, dimensions: dict,
                        length_m: float, drop_height_m: float, dropped_mass_kg: float,
                        environment: str = "normal") -> str:
    """Simulera ett falltest: uppskattar stötkraften när ett föremål tappas
    från en given höjd, och kör den kraften genom samma balkmodell som
    test_durability för att avgöra om det håller. Bygger på energimetoden
    (rörelseenergi vid nedslag omvandlas till deformationsarbete i
    strukturen) — en vanlig ingenjörsapproximation för stöttest, inte en
    fullständig transient FEA-simulering."""
    mats = _load_materials()
    mat_key = material.lower().strip()
    if mat_key not in mats:
        return f"Material '{material}' finns inte. Använd list_materials."
    mat = mats[mat_key]
    env_key = environment.lower().strip()
    if env_key not in ENVIRONMENTS:
        return f"Okänd miljö '{environment}'. Giltiga: {', '.join(ENVIRONMENTS.keys())}"
    env = ENVIRONMENTS[env_key]
    E = mat["young_modulus_pa"] * env["modulus_mult"]

    cross_section = cross_section.lower().strip()
    if cross_section == "rectangular":
        w, h = dimensions["width_m"], dimensions["height_m"]
        I = (w * h ** 3) / 12.0
    elif cross_section == "circular":
        r = dimensions["radius_m"]
        I = (math.pi * r ** 4) / 4.0
    else:
        return "cross_section måste vara 'rectangular' eller 'circular'."

    # Strukturens styvhet för en cantilever med ändlast: k = 3EI / L^3
    k = 3 * E * I / length_m ** 3
    g = 9.81
    impact_velocity = math.sqrt(2 * g * drop_height_m)
    # Energimetoden: mgh (fallenergi) = 1/2 * k * delta_max^2  ->  delta_max
    fall_energy_j = dropped_mass_kg * g * drop_height_m
    peak_deflection_m = math.sqrt(2 * fall_energy_j / k) if k > 0 else float("inf")
    peak_force_n = k * peak_deflection_m
    peak_g_force = peak_force_n / (dropped_mass_kg * g)

    stress_result = json.loads(test_durability.invoke({
        "material": material, "cross_section": cross_section, "dimensions": dimensions,
        "length_m": length_m, "load_n": peak_force_n, "environment": environment,
    }))

    return json.dumps({
        "drop_height_m": drop_height_m,
        "dropped_mass_kg": dropped_mass_kg,
        "impact_velocity_m_s": round(impact_velocity, 3),
        "estimated_peak_impact_force_n": round(peak_force_n, 2),
        "estimated_peak_deceleration_g": round(peak_g_force, 1),
        "peak_deflection_mm": round(peak_deflection_m * 1000, 3),
        "resulting_stress_analysis": stress_result.get("stress_analysis"),
        "verdict": stress_result.get("verdict"),
        "context": (
            "Jämförelse: ett obelagt mobiltelefonfall (~1.5m) ger ofta 50-200g "
            "toppacceleration beroende på yta. Under ~15g är det en mjuk stöt, "
            "över ~300g är det en mycket hård smäll för de flesta konsumentprodukter."
        ),
        "model_note": (
            "Energimetod (fallenergi = fjäderenergi i strukturen), linjärt "
            "fjäderbeteende antaget. Verkligt stötförlopp är olinjärt, kortare "
            "och beror starkt på underlag/vinkel — se detta som en övre "
            "grov uppskattning, inte en exakt siffra."
        ),
    }, ensure_ascii=False)


class FatigueLifeInput(BaseModel):
    material: str = Field(description="Materialnyckel")
    applied_stress_mpa: float = Field(description="Cyklisk spänningsamplitud i MPa (t.ex. från test_durability)")
    environment: str = Field(default="normal", description=f"En av: {', '.join(ENVIRONMENTS.keys())}")


@tool("estimate_fatigue_life", args_schema=FatigueLifeInput)
def estimate_fatigue_life(material: str, applied_stress_mpa: float, environment: str = "normal") -> str:
    """Uppskatta antal belastningscykler till utmattningsbrott vid en given
    cyklisk spänning, med en förenklad Basquin-liknande S-N-kurva
    (log-linjär mellan sträckgräns vid 1000 cykler och utmattningsgräns vid
    1e6+ cykler). Grov ingenjörsapproximation, inte labbverifierad S-N-data."""
    mats = _load_materials()
    mat_key = material.lower().strip()
    if mat_key not in mats:
        return f"Material '{material}' finns inte. Använd list_materials."
    mat = mats[mat_key]
    env_key = environment.lower().strip()
    if env_key not in ENVIRONMENTS:
        return f"Okänd miljö '{environment}'. Giltiga: {', '.join(ENVIRONMENTS.keys())}"
    env = ENVIRONMENTS[env_key]

    yield_strength_mpa = (mat["yield_strength_pa"] * env["yield_mult"]) / 1e6
    fatigue_ratio = mat.get("fatigue_limit_ratio", 0.35)
    fatigue_limit_mpa = yield_strength_mpa * fatigue_ratio
    sigma = applied_stress_mpa

    if sigma <= fatigue_limit_mpa:
        return json.dumps({
            "material": mat_key, "applied_stress_mpa": sigma,
            "fatigue_limit_mpa": round(fatigue_limit_mpa, 2),
            "estimated_cycles_to_failure": "Praktiskt taget obegränsat (>1e7)",
            "verdict": "Under utmattningsgränsen — förväntas hålla för i princip obegränsat antal cykler.",
        }, ensure_ascii=False)

    if sigma >= yield_strength_mpa:
        return json.dumps({
            "material": mat_key, "applied_stress_mpa": sigma,
            "yield_strength_mpa": round(yield_strength_mpa, 2),
            "estimated_cycles_to_failure": "< 1000 (plastisk deformation/brott redan vid enstaka belastning)",
            "verdict": "Spänningen överstiger sträckgränsen — håller inte ens för en enda belastning.",
        }, ensure_ascii=False)

    # Log-linjär interpolation mellan (N=1e3, sigma=yield) och (N=1e6, sigma=fatigue_limit)
    log_n1, log_n2 = 3.0, 6.0
    s1, s2 = yield_strength_mpa, fatigue_limit_mpa
    frac = (sigma - s2) / (s1 - s2) if s1 != s2 else 0
    log_n = log_n2 - frac * (log_n2 - log_n1)
    cycles = 10 ** log_n

    return json.dumps({
        "material": mat_key,
        "environment": env_key,
        "applied_stress_mpa": sigma,
        "yield_strength_mpa": round(yield_strength_mpa, 2),
        "fatigue_limit_mpa": round(fatigue_limit_mpa, 2),
        "estimated_cycles_to_failure": int(cycles),
        "verdict": f"Förväntad livslängd ~{cycles:,.0f} belastningscykler vid denna spänning.".replace(",", " "),
        "model_note": (
            "Grov log-linjär Basquin-approximation mellan sträckgräns (N≈1000) och "
            "utmattningsgräns (N≈1e6). Använd som indikation, inte dimensioneringsunderlag."
        ),
    }, ensure_ascii=False)


class CompareMaterialsInput(BaseModel):
    materials: list[str] = Field(description="Lista av materialnycklar att jämföra")
    cross_section: str = Field(description="'rectangular' eller 'circular'")
    dimensions: dict = Field(description="Se test_durability för format")
    length_m: float = Field(description="Fri längd i meter")
    load_n: float = Field(description="Last i Newton")
    environment: str = Field(default="normal")


@tool("compare_materials_durability", args_schema=CompareMaterialsInput)
def compare_materials_durability(materials: list[str], cross_section: str, dimensions: dict,
                                  length_m: float, load_n: float, environment: str = "normal") -> str:
    """Kör test_durability för flera material på samma geometri/last/miljö
    och returnera en jämförelsetabell, sorterad efter säkerhetsfaktor."""
    results = []
    for m in materials:
        raw = test_durability.invoke({
            "material": m, "cross_section": cross_section, "dimensions": dimensions,
            "length_m": length_m, "load_n": load_n, "environment": environment,
        })
        try:
            results.append(json.loads(raw))
        except json.JSONDecodeError:
            results.append({"material": m, "error": raw})

    results.sort(key=lambda r: r.get("safety_factor", -1), reverse=True)
    return json.dumps(results, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 4) PARAMETRISKA DELMALLAR (case är bara EN av flera — bygg vad som helst:
#    fodral, hyllhållare/vinkelbeslag, monteringsplattor, m.m.)
# --------------------------------------------------------------------------- #
# Fyra verktyg som täcker de vanligaste produktdesign-formerna:
#   - create_case_shell   : ihåligt skal runt ett objekt (fodral, hölje, box)
#   - create_bracket       : L-vinkel/hyllhållare med hål, två armar i 90°
#   - create_flat_plate    : platt platta/fot/monteringsbleck med hål
#   - add_mounting_holes   : stansa hål i VILKEN modell som helst i efterhand
#     (funkar på case, bracket, plate, eller något skapat med create_3d_model/
#     combine_3d_models — gör hela verktygslådan generell, inte case-specifik)
# Alla arbetar i millimeter (STL-standard) och kräver 'manifold3d'.

def _rounded_rect_prism(width_mm: float, height_mm: float, depth_mm: float,
                         corner_radius_mm: float) -> "trimesh.Trimesh":
    """Extruderad rundad rektangel (rundade sidor, platt topp/botten) —
    det vanliga formspråket för fodral/skal."""
    r = min(corner_radius_mm, width_mm / 2 - 0.01, height_mm / 2 - 0.01)
    r = max(r, 0.01)
    hw, hh = width_mm / 2 - r, height_mm / 2 - r
    circle = Point(0, 0).buffer(r, resolution=16)
    corners = [
        Point(hw, hh).buffer(r, resolution=16),
        Point(-hw, hh).buffer(r, resolution=16),
        Point(hw, -hh).buffer(r, resolution=16),
        Point(-hw, -hh).buffer(r, resolution=16),
    ]
    profile = unary_union(corners).convex_hull
    return trimesh.creation.extrude_polygon(profile, height=depth_mm)


class CreateCaseInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    outer_width_mm: float = Field(description="Yttre bredd (mm) — hämta gärna via lookup_reference_shape")
    outer_height_mm: float = Field(description="Yttre höjd (mm)")
    outer_depth_mm: float = Field(description="Yttre djup (mm)")
    corner_radius_mm: float = Field(default=6.0, description="Hörnradie (mm)")
    wall_thickness_mm: float = Field(default=1.6, description="Väggtjocklek (mm), TPU-skal brukar vara 1.2-2.0mm")
    open_top: bool = Field(default=True, description="Om True: öppen ovansida (fickfodral). Om False: helt sluten hålighet.")
    material: str = Field(default="tpu_95a", description="Materialnyckel för massa/printtid, se list_materials")
    port_cutout: Optional[dict] = Field(
        default=None,
        description=(
            "Valfritt urtag t.ex. för laddport: {'width_mm','height_mm','x_offset_mm',"
            "'y_offset_mm'} centrerat på en långsida (z-axeln)."
        ),
    )


@tool("create_case_shell", args_schema=CreateCaseInput)
def create_case_shell(name: str, outer_width_mm: float, outer_height_mm: float,
                       outer_depth_mm: float, corner_radius_mm: float = 6.0,
                       wall_thickness_mm: float = 1.6, open_top: bool = True,
                       material: str = "tpu_95a",
                       port_cutout: Optional[dict] = None) -> str:
    """Skapa ett rundat skal/fodral (t.ex. TPU-skal till hörlursfodral) runt
    ett innerobjekts yttermått. Använd lookup_reference_shape först för att
    få rätt mått på det du bygger runt. Genererar en riktig, ihålig,
    utskrivbar STL-modell med angiven väggtjocklek, och valfritt urtag för
    t.ex. laddningsport. Returnerar en utförlig rapport: geometri, massa,
    ungefärlig printtid och rekommenderad väggtjocklek-bedömning. Kräver
    'manifold3d' installerat."""
    outer = _rounded_rect_prism(outer_width_mm, outer_height_mm, outer_depth_mm,
                                 corner_radius_mm)

    inner_w = outer_width_mm - 2 * wall_thickness_mm
    inner_h = outer_height_mm - 2 * wall_thickness_mm
    inner_r = max(corner_radius_mm - wall_thickness_mm, 0.5)
    floor = wall_thickness_mm if open_top else wall_thickness_mm
    inner_depth = outer_depth_mm - floor - (0 if open_top else wall_thickness_mm)
    if inner_w <= 0 or inner_h <= 0 or inner_depth <= 0:
        return "Väggtjockleken är för stor för de angivna yttermåtten."

    cavity = _rounded_rect_prism(inner_w, inner_h, inner_depth, inner_r)
    # Placera håligheten så att den lämnar 'floor' mm botten (och tak om stängd)
    z_shift = -outer_depth_mm / 2 + floor + inner_depth / 2
    cavity.apply_translation([0, 0, z_shift])
    if open_top:
        # Förläng håligheten uppåt genom locket så toppen blir öppen
        cavity.apply_translation([0, 0, wall_thickness_mm])
        stretch = trimesh.creation.box(extents=[inner_w, inner_h, wall_thickness_mm * 4])
        stretch.apply_translation([0, 0, outer_depth_mm / 2])
        cavity = trimesh.boolean.union([cavity, stretch])

    try:
        result = trimesh.boolean.difference([outer, cavity])
    except Exception as e:
        return f"Boolesk operation misslyckades ({e}). Kontrollera att 'manifold3d' är installerat."

    if port_cutout:
        pw, ph = port_cutout["width_mm"], port_cutout["height_mm"]
        xo, yo = port_cutout.get("x_offset_mm", 0), port_cutout.get("y_offset_mm", 0)
        cutter = trimesh.creation.box(extents=[pw, ph, wall_thickness_mm * 4])
        cutter.apply_translation([xo, yo, -outer_depth_mm / 2])
        try:
            result = trimesh.boolean.difference([result, cutter])
        except Exception as e:
            return f"Kunde inte skapa portutrag ({e})."

    fpath = MODELS_DIR / f"{name}.stl"
    result.export(fpath)

    report = {
        "file_path": str(fpath),
        "outer_dims_mm": [outer_width_mm, outer_height_mm, outer_depth_mm],
        "inner_cavity_dims_mm": [round(inner_w, 2), round(inner_h, 2), round(inner_depth, 2)],
        "wall_thickness_mm": wall_thickness_mm,
    }
    report.update(_geometry_report(result, material_key=material, volume_unit_is_mm3=True))

    # Enkel bedömning av väggtjocklek för vanliga printmaterial
    if wall_thickness_mm < 1.0:
        wall_verdict = "TUNN — risk för sprickbildning/genomslag vid stötar, särskilt i styva material."
    elif wall_thickness_mm <= 2.5:
        wall_verdict = "NORMAL — vanligt intervall för skal/case i TPU eller ABS."
    else:
        wall_verdict = "TJOCK — stabil men tyngre och mer materialåtgång, ovanligt för ett bärbart skal."
    report["wall_thickness_assessment"] = wall_verdict

    report["tip"] = ("Kör render_model_views eller generate_shape_report för att "
                      "granska formen visuellt/textuellt, och test_durability eller "
                      "simulate_drop_test (SI-enheter, meter) för att stresstesta väggarna.")
    return json.dumps(report, ensure_ascii=False)


class CreateBracketInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    arm1_length_mm: float = Field(description="Längd på arm 1 (t.ex. den som bär hyllan)")
    arm2_length_mm: float = Field(description="Längd på arm 2 (t.ex. den som skruvas i väggen)")
    thickness_mm: float = Field(description="Materialtjocklek (mm)")
    depth_mm: float = Field(description="Djup/bredd på beslaget rakt ut ur skärmen (mm)")
    material: str = Field(default="aluminum_6061", description="Materialnyckel, se list_materials")
    holes: Optional[list] = Field(
        default=None,
        description=(
            "Valfria monteringshål: lista av {'x_mm','y_mm','diameter_mm'} i "
            "profilens 2D-koordinater. x=0,y=0 är innerhörnet. Arm1 ligger längs "
            "x-axeln (0..arm1_length_mm), arm2 längs y-axeln (0..arm2_length_mm)."
        ),
    )


@tool("create_bracket", args_schema=CreateBracketInput)
def create_bracket(name: str, arm1_length_mm: float, arm2_length_mm: float,
                    thickness_mm: float, depth_mm: float,
                    material: str = "aluminum_6061",
                    holes: Optional[list] = None) -> str:
    """Skapa ett L-format vinkelbeslag/hyllhållare — två armar i 90 graders
    vinkel med given materialtjocklek, t.ex. för att hålla upp en hylla eller
    montera något i en vägg. Lägg gärna till monteringshål via 'holes', eller
    stansa fler i efterhand med add_mounting_holes. Kräver 'manifold3d'."""
    if thickness_mm >= arm1_length_mm or thickness_mm >= arm2_length_mm:
        return "thickness_mm måste vara mindre än båda armlängderna."

    profile_pts = [
        (0, 0), (arm1_length_mm, 0), (arm1_length_mm, thickness_mm),
        (thickness_mm, thickness_mm), (thickness_mm, arm2_length_mm), (0, arm2_length_mm),
    ]
    from shapely.geometry import Polygon
    profile = Polygon(profile_pts)
    mesh = trimesh.creation.extrude_polygon(profile, height=depth_mm)

    if holes:
        for h in holes:
            d = h["diameter_mm"]
            cutter = trimesh.creation.cylinder(radius=d / 2, height=depth_mm * 4, sections=24)
            cutter.apply_translation([h["x_mm"], h["y_mm"], depth_mm / 2])
            try:
                mesh = trimesh.boolean.difference([mesh, cutter])
            except Exception as e:
                return f"Kunde inte skapa hål vid ({h['x_mm']},{h['y_mm']}): {e}"

    fpath = MODELS_DIR / f"{name}.stl"
    mesh.export(fpath)

    report = {
        "file_path": str(fpath),
        "arm1_length_mm": arm1_length_mm,
        "arm2_length_mm": arm2_length_mm,
        "thickness_mm": thickness_mm,
        "depth_mm": depth_mm,
        "num_holes": len(holes) if holes else 0,
    }
    report.update(_geometry_report(mesh, material_key=material, volume_unit_is_mm3=True))
    report["tip"] = (
        "Testa hållfastheten med test_durability: sätt length_m = arm1_length_mm/1000 "
        "(hyllarmen som utsätts för böjlast), cross_section='rectangular', "
        "dimensions={'width_m': depth_mm/1000, 'height_m': thickness_mm/1000}."
    )
    return json.dumps(report, ensure_ascii=False)


class CreateFlatPlateInput(BaseModel):
    name: str = Field(description="Filnamn (utan filändelse)")
    width_mm: float = Field(description="Bredd (mm)")
    height_mm: float = Field(description="Höjd (mm)")
    thickness_mm: float = Field(description="Tjocklek (mm)")
    corner_radius_mm: float = Field(default=2.0, description="Hörnradie (mm), 0 = vassa hörn")
    material: str = Field(default="aluminum_6061", description="Materialnyckel, se list_materials")
    holes: Optional[list] = Field(
        default=None,
        description="Valfria hål: lista av {'x_mm','y_mm','diameter_mm'}, origo i plattans centrum.",
    )


@tool("create_flat_plate", args_schema=CreateFlatPlateInput)
def create_flat_plate(name: str, width_mm: float, height_mm: float, thickness_mm: float,
                       corner_radius_mm: float = 2.0, material: str = "aluminum_6061",
                       holes: Optional[list] = None) -> str:
    """Skapa en platt (ev. rundad) platta/bleck/fot med valfria hål — grunden
    för monteringsplattor, fötter, mellanlägg, väggfästen och liknande.
    Kombinera med add_mounting_holes för att lägga till fler hål senare, eller
    med combine_3d_models för att bygga ihop plattan med andra delar."""
    plate = _rounded_rect_prism(width_mm, height_mm, thickness_mm, corner_radius_mm)

    if holes:
        for h in holes:
            d = h["diameter_mm"]
            cutter = trimesh.creation.cylinder(radius=d / 2, height=thickness_mm * 4, sections=24)
            cutter.apply_translation([h["x_mm"], h["y_mm"], 0])
            try:
                plate = trimesh.boolean.difference([plate, cutter])
            except Exception as e:
                return f"Kunde inte skapa hål vid ({h['x_mm']},{h['y_mm']}): {e}"

    fpath = MODELS_DIR / f"{name}.stl"
    plate.export(fpath)

    report = {"file_path": str(fpath), "num_holes": len(holes) if holes else 0}
    report.update(_geometry_report(plate, material_key=material, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


class AddMountingHolesInput(BaseModel):
    file_path: str = Field(description="Sökväg till en existerande STL/OBJ-fil (case, bracket, plate, eller vad som helst)")
    holes: list = Field(
        description=(
            "Lista av hål: {'x_mm','y_mm' eller 'z_mm' (de två koordinaterna i planet "
            "vinkelrätt mot 'axis'), 'diameter_mm'}. Exempel för axis='z': "
            "{'x_mm':10,'y_mm':5,'diameter_mm':3.2}."
        )
    )
    axis: str = Field(default="z", description="Vilken axel hålen borras rakt igenom: 'x', 'y' eller 'z'")
    name: Optional[str] = Field(default=None, description="Filnamn för resultatet (annars skrivs originalet över)")


@tool("add_mounting_holes", args_schema=AddMountingHolesInput)
def add_mounting_holes(file_path: str, holes: list, axis: str = "z",
                        name: Optional[str] = None) -> str:
    """Stansa ett eller flera cylindriska hål (t.ex. skruvhål) rakt igenom EN
    GODTYCKLIG modell — fungerar på case, brackets, plattor eller vad som
    helst som redan skapats. Detta gör verktygslådan generell: bygg valfri
    grundform (create_3d_model, create_case_shell, create_bracket,
    create_flat_plate, combine_3d_models) och lägg sedan till exakt de hål
    du behöver, var du behöver dem. OBS: hålkoordinater/diametrar tolkas i
    modellens egna enheter — funkar rakt av på mm-skalade delar (case,
    bracket, plate), men om modellen skapades i meter (create_3d_model)
    måste du ange mått i meter istället, trots parameternamnen. Kräver
    'manifold3d'."""
    mesh = trimesh.load(file_path, force="mesh")
    bounds = mesh.bounds
    axis = axis.lower().strip()
    axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis)
    if axis_idx is None:
        return "axis måste vara 'x', 'y' eller 'z'."

    span = bounds[1][axis_idx] - bounds[0][axis_idx]
    hole_length = span * 4 + 10  # garanterat genom hela modellen
    other_axes = [i for i in (0, 1, 2) if i != axis_idx]
    key_names = {0: "x_mm", 1: "y_mm", 2: "z_mm"}

    for h in holes:
        d = h["diameter_mm"]
        cutter = trimesh.creation.cylinder(radius=d / 2, height=hole_length, sections=24)
        # cylinder skapas längs Z som standard - rotera om vi borrar längs X eller Y
        if axis_idx == 0:
            cutter.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
        elif axis_idx == 1:
            cutter.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
        pos = [0, 0, 0]
        pos[other_axes[0]] = h[key_names[other_axes[0]]]
        pos[other_axes[1]] = h[key_names[other_axes[1]]]
        pos[axis_idx] = (bounds[0][axis_idx] + bounds[1][axis_idx]) / 2
        cutter.apply_translation(pos)
        try:
            mesh = trimesh.boolean.difference([mesh, cutter])
        except Exception as e:
            return f"Kunde inte stansa hål: {e}"

    fpath = MODELS_DIR / f"{name or Path(file_path).stem}.stl"
    mesh.export(fpath)
    report = {"file_path": str(fpath), "holes_added": len(holes), "axis": axis}
    report.update(_geometry_report(mesh, volume_unit_is_mm3=True))
    return json.dumps(report, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 5) VISUALISERING — så en (icke-multimodal) AI eller en människa kan
#    "se" modellen
# --------------------------------------------------------------------------- #
# Två kompletterande spår:
#   a) render_model_views  -> PNG-bilder (för människan, eller för en AI som
#      FAKTISKT är multimodal och kan granska bilden i efterhand)
#   b) generate_shape_report -> ren text (mått, tvärsnitt vid olika höjder,
#      väggtjocklek) som en icke-multimodal AI kan resonera kring direkt
#   c) create_html_viewer -> fristående HTML-fil med 3D-rotation i webbläsare,
#      för människan att själv besiktiga modellen

class RenderViewsInput(BaseModel):
    file_path: str = Field(description="Sökväg till STL/OBJ-fil")
    name: Optional[str] = Field(default=None, description="Filnamnsprefix för bilderna")


@tool("render_model_views", args_schema=RenderViewsInput)
def render_model_views(file_path: str, name: Optional[str] = None) -> str:
    """Rendera modellen från fyra vinklar (fram, ovanifrån, sida, isometrisk)
    till PNG-bilder som sparas på disk. Använd detta för att visuellt
    kontrollera en modell — antingen själv (om du kan se bilder) eller för
    att låta användaren öppna PNG-filerna."""
    mesh = trimesh.load(file_path, force="mesh")
    prefix = name or Path(file_path).stem
    views = {
        "front": (0, 0), "top": (90, 0), "side": (0, 90), "iso": (30, 45),
    }
    paths = {}
    for view_name, (elev, azim) in views.items():
        fig = plt.figure(figsize=(5, 5))
        ax = fig.add_subplot(111, projection="3d")
        collection = Poly3DCollection(mesh.vertices[mesh.faces], alpha=0.9,
                                       facecolor="#8fb8de", edgecolor="#33475b", linewidths=0.15)
        ax.add_collection3d(collection)
        bounds = mesh.bounds
        ax.set_xlim(bounds[0][0], bounds[1][0])
        ax.set_ylim(bounds[0][1], bounds[1][1])
        ax.set_zlim(bounds[0][2], bounds[1][2])
        ax.set_box_aspect(np.ptp(bounds, axis=0))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        out = RENDERS_DIR / f"{prefix}_{view_name}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        paths[view_name] = str(out)
    return json.dumps(paths, ensure_ascii=False)


class ShapeReportInput(BaseModel):
    file_path: str = Field(description="Sökväg till STL/OBJ-fil")
    num_slices: int = Field(default=8, description="Antal horisontella tvärsnitt att analysera")


@tool("generate_shape_report", args_schema=ShapeReportInput)
def generate_shape_report(file_path: str, num_slices: int = 8) -> str:
    """Generera en textbaserad formbeskrivning av modellen: yttermått,
    volym, samt tvärsnittsstorlek vid N höjder längs Z-axeln. Detta ger en
    icke-multimodal AI (som inte kan se bilder) tillräckligt med information
    för att resonera om formen, symmetri och proportioner utan att behöva
    en bild."""
    mesh = trimesh.load(file_path, force="mesh")
    bounds = mesh.bounds
    extents = mesh.extents
    z_min, z_max = bounds[0][2], bounds[1][2]
    heights = np.linspace(z_min, z_max, num_slices + 2)[1:-1]

    slices = []
    for z in heights:
        section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        if section is None:
            slices.append({"z_mm": round(float(z), 2), "note": "inget tvärsnitt (utanför geometrin)"})
            continue
        planar, _ = section.to_planar()
        sb = planar.bounds
        slices.append({
            "z_mm": round(float(z), 2),
            "cross_section_width_mm": round(float(sb[1][0] - sb[0][0]), 2),
            "cross_section_height_mm": round(float(sb[1][1] - sb[0][1]), 2),
            "cross_section_area_mm2": round(float(planar.area), 2),
        })

    report = {
        "file_path": file_path,
        "bounding_box_mm": [round(float(v), 2) for v in extents],
        "volume_mm3": round(float(mesh.volume), 2),
        "surface_area_mm2": round(float(mesh.area), 2),
        "watertight": bool(mesh.is_watertight),
        "center_of_mass_mm": [round(float(v), 2) for v in mesh.center_mass],
        "cross_sections_bottom_to_top": slices,
        "reading_guide": (
            "cross_section_width/height beskriver konturens utbredning i XY-planet "
            "vid varje Z-höjd. Jämn förändring = konisk/rundad form. Plötsliga hopp "
            "= kant, urtag eller separat detalj (t.ex. ett hak för laddport)."
        ),
    }
    return json.dumps(report, indent=2, ensure_ascii=False)


class HtmlViewerInput(BaseModel):
    file_path: str = Field(description="Sökväg till STL-fil")
    name: Optional[str] = Field(default=None, description="Filnamn för HTML-filen (utan .html)")


@tool("create_html_viewer", args_schema=HtmlViewerInput)
def create_html_viewer(file_path: str, name: Optional[str] = None) -> str:
    """Skapa en fristående HTML-fil med en roterbar 3D-vy (three.js) av en
    STL-modell, så en människa kan öppna filen i valfri webbläsare och
    besiktiga modellen visuellt — utan att behöva någon 3D-mjukvara."""
    mesh = trimesh.load(file_path, force="mesh")
    verts = mesh.vertices.tolist()
    faces = mesh.faces.tolist()
    fname = f"{name or Path(file_path).stem}_viewer.html"
    fpath = MODELS_DIR / fname

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>3D-modellvisare — {Path(file_path).name}</title>
<style>body{{margin:0;background:#1a1d23;overflow:hidden;font-family:sans-serif}}
#info{{position:absolute;top:10px;left:10px;color:#ccc;font-size:13px}}</style></head>
<body>
<div id="info">{Path(file_path).name} — dra för att rotera, scrolla för att zooma</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/examples/js/controls/OrbitControls.js"></script>
<script>
const verts = {json.dumps(verts)};
const faces = {json.dumps(faces)};

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1d23);
const camera = new THREE.PerspectiveCamera(45, window.innerWidth/window.innerHeight, 0.1, 10000);
const renderer = new THREE.WebGLRenderer({{antialias:true}});
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const geometry = new THREE.BufferGeometry();
const positions = new Float32Array(verts.flat());
geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
geometry.setIndex(faces.flat());
geometry.computeVertexNormals();

const material = new THREE.MeshStandardMaterial({{color:0x8fb8de, metalness:0.1, roughness:0.6, side:THREE.DoubleSide}});
const mesh = new THREE.Mesh(geometry, material);
scene.add(mesh);

geometry.computeBoundingSphere();
const r = geometry.boundingSphere.radius;
const center = geometry.boundingSphere.center;
camera.position.set(center.x + r*2, center.y + r*2, center.z + r*2);

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
dirLight.position.set(1,1,1);
scene.add(dirLight);
const grid = new THREE.GridHelper(r*4, 20);
scene.add(grid);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.copy(center);
controls.update();

window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth/window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});

function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body></html>"""

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return f"HTML-viewer skapad: {fpath} (öppna i valfri webbläsare)"


# --------------------------------------------------------------------------- #
# Registrering
# --------------------------------------------------------------------------- #

def get_tools() -> list:
    """Returnerar alla verktyg, redo att skickas till en LangChain-agent."""
    return [
        create_3d_model,
        combine_3d_models,
        lookup_reference_shape,
        add_reference_shape,
        download_reference_shape,
        create_case_shell,
        create_bracket,
        create_flat_plate,
        add_mounting_holes,
        render_model_views,
        generate_shape_report,
        create_html_viewer,
        list_materials,
        add_material,
        download_material,
        test_durability,
        simulate_drop_test,
        estimate_fatigue_life,
        compare_materials_durability,
    ]


if __name__ == "__main__":
    # Snabbt självtest utan LLM
    print(create_3d_model.invoke({
        "shape": "box", "dimensions": {"x": 0.02, "y": 0.05, "z": 1.0}, "name": "test_beam",
    }))
    print(list_materials.invoke({}))
    print(test_durability.invoke({
        "material": "aluminum_6061", "cross_section": "rectangular",
        "dimensions": {"width_m": 0.02, "height_m": 0.05},
        "length_m": 1.0, "load_n": 500, "environment": "normal",
    }))
    print(compare_materials_durability.invoke({
        "materials": ["aluminum_6061", "steel_mild", "pla", "wood_pine"],
        "cross_section": "rectangular",
        "dimensions": {"width_m": 0.02, "height_m": 0.05},
        "length_m": 1.0, "load_n": 500, "environment": "humid",
    }))
