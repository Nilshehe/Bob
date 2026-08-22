"""
gui_tools.py
De konkreta GUI-verktyg Bob får använda. ENDA filen du behöver röra för
att ge Bob en helt ny förmåga — skriv en funktion, dekorera med @tool,
klart. Ingen annan kod behöver ändras.
"""
import uuid

from gui.backend.registry import tool, variable
from gui.backend.state_manager import state
import gui.backend.gui_server as gui_server
import gui.backend.window_manager as wm


def _wid_for_element(element_id):
    el = state.get_element(element_id)
    return el.get("window_id") if el else None


@tool(
    "Skapa ett nytt GUI-element (text, button, panel, status, input eller 3d) i ett fönster.",
    parameters={
        "element_type": {"type": "string", "enum": ["text", "button", "panel", "status", "input", "3d"]},
        "window_id": {"type": "string", "description": "Vilket fönster elementet ska skapas i"},
        "x": {"type": "integer"}, "y": {"type": "integer"},
        "w": {"type": "integer"}, "h": {"type": "integer"},
        "label": {"type": "string", "description": "Text/etikett på elementet"},
        "element_id": {"type": "string", "description": "Valfritt eget id, annars genereras ett"},
    },
    required=["element_type", "window_id"],
)
def create_element(element_type, window_id, x=40, y=40, w=200, h=80, label="", element_id=None):
    element_id = element_id or f"{element_type}_{uuid.uuid4().hex[:6]}"
    state.upsert_element(
        element_id, type=element_type, window_id=window_id,
        x=x, y=y, w=w, h=h, label=label, visible=True, props={},
    )
    gui_server.manager.send(window_id, {"type": "create_element", "element_id": element_id,
                                         **state.get_element(element_id)})
    return {"element_id": element_id}


@tool(
    "Ta bort eller dölj ett GUI-element. permanent=False döljer det bara "
    "(det finns kvar och kan visas igen), permanent=True raderar det helt.",
    parameters={
        "element_id": {"type": "string"},
        "permanent": {"type": "boolean"},
    },
    required=["element_id"],
)
def remove_element(element_id, permanent=False):
    window_id = _wid_for_element(element_id)
    if permanent:
        state.remove_element(element_id)
    else:
        state.upsert_element(element_id, visible=False)
    if window_id:
        gui_server.manager.send(window_id, {"type": "remove_element", "element_id": element_id, "permanent": permanent})
    return {"ok": True}


@tool(
    "Visa ett tidigare dolt element igen.",
    parameters={"element_id": {"type": "string"}},
    required=["element_id"],
)
def show_element(element_id):
    el = state.get_element(element_id)
    if not el:
        raise ValueError(f"Okänt element: {element_id}")
    state.upsert_element(element_id, visible=True)
    gui_server.manager.send(el["window_id"], {"type": "create_element", "element_id": element_id,
                                               **state.get_element(element_id)})
    return {"ok": True}


@tool(
    "Flytta ett befintligt GUI-element till en ny position (skärm-koordinater inom fönstret).",
    parameters={"element_id": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}},
    required=["element_id", "x", "y"],
)
def move_element(element_id, x, y):
    window_id = _wid_for_element(element_id)
    state.upsert_element(element_id, x=x, y=y)
    if window_id:
        gui_server.manager.send(window_id, {"type": "move_element", "element_id": element_id, "x": x, "y": y})
    return {"ok": True}


@tool(
    "Uppdatera egenskaper hos ett element: storlek, text/etikett, synlighet "
    "eller fria extra-egenskaper (t.ex. färg).",
    parameters={
        "element_id": {"type": "string"},
        "w": {"type": "integer"}, "h": {"type": "integer"},
        "label": {"type": "string"}, "visible": {"type": "boolean"},
        "props": {"type": "object", "description": "Fria extra-egenskaper, t.ex. {'color': '#00eaff'}"},
    },
    required=["element_id"],
)
def update_element(element_id, w=None, h=None, label=None, visible=None, props=None):
    fields = {}
    if w is not None: fields["w"] = w
    if h is not None: fields["h"] = h
    if label is not None: fields["label"] = label
    if visible is not None: fields["visible"] = visible
    if props:
        current = state.get_element(element_id) or {}
        fields["props"] = {**current.get("props", {}), **props}
    state.upsert_element(element_id, **fields)
    window_id = _wid_for_element(element_id)
    if window_id:
        gui_server.manager.send(window_id, {"type": "update_element", "element_id": element_id, **fields})
    return {"ok": True}


@tool(
    "Skapa ett nytt GUI-fönster, ev. på en specifik skärm.",
    parameters={
        "title": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"},
        "screen": {"type": "integer", "description": "Skärmindex från get_screens()"},
    },
)
def create_window(title="Bob", width=900, height=600, screen=None):
    window_id = wm.create_window(title=title, width=width, height=height, screen=screen)
    return {"window_id": window_id}


@tool(
    "Flytta ett fönster till nya koordinater eller till en annan skärm.",
    parameters={
        "window_id": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"},
        "screen": {"type": "integer", "description": "Skärmindex, t.ex. 0 eller 1"},
    },
    required=["window_id"],
)
def move_window(window_id, x=None, y=None, screen=None):
    wm.move_window(window_id, x=x, y=y, screen=screen)
    return {"ok": True}


@tool(
    "Stäng ett GUI-fönster (och de element som hör till det).",
    parameters={"window_id": {"type": "string"}},
    required=["window_id"],
)
def close_window(window_id):
    wm.close_window(window_id)
    return {"ok": True}


@tool("Lista anslutna skärmar med upplösning och position.")
def get_screens():
    return wm.get_screens()


@tool(
    "Ladda och rendera en 3D-modell (glb/gltf) i hologramstil i ett fönster.",
    parameters={
        "model_path": {"type": "string", "description": "Sökväg eller URL till 3D-modellfilen"},
        "window_id": {"type": "string"},
        "element_id": {"type": "string"},
        "x": {"type": "integer"}, "y": {"type": "integer"},
    },
    required=["model_path", "window_id"],
)
def load_3d_model(model_path, window_id, element_id=None, x=200, y=100):
    element_id = element_id or f"3d_{uuid.uuid4().hex[:6]}"
    state.upsert_element(
        element_id, type="3d", window_id=window_id, x=x, y=y, w=400, h=400,
        visible=True, label="3D",
        props={"model_path": model_path, "color": "#00eaff",
               "wireframe": True, "position3d": [0, 0, 0], "scale": 1.0},
    )
    gui_server.manager.send(window_id, {"type": "create_element", "element_id": element_id,
                                         **state.get_element(element_id)})
    return {"element_id": element_id}


@tool(
    "Flytta en redan laddad 3D-modell inuti sin egen scen (3D-koordinater, inte skärm-koordinater).",
    parameters={
        "element_id": {"type": "string"},
        "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
    },
    required=["element_id", "x", "y", "z"],
)
def move_3d_model(element_id, x, y, z):
    el = state.get_element(element_id)
    if not el:
        raise ValueError(f"Okänt element: {element_id}")
    props = {**el.get("props", {}), "position3d": [x, y, z]}
    state.upsert_element(element_id, props=props)
    gui_server.manager.send(el["window_id"], {"type": "update_element", "element_id": element_id, "props": props})
    return {"ok": True}


@tool(
    "Ändra färg, wireframe-läge eller genomskinlighet på en laddad 3D-modell.",
    parameters={
        "element_id": {"type": "string"},
        "color": {"type": "string", "description": "Hex-färg, t.ex. '#00eaff'"},
        "wireframe": {"type": "boolean"},
        "opacity": {"type": "number"},
    },
    required=["element_id"],
)
def set_3d_model_style(element_id, color=None, wireframe=None, opacity=None):
    el = state.get_element(element_id)
    if not el:
        raise ValueError(f"Okänt element: {element_id}")
    props = dict(el.get("props", {}))
    if color is not None: props["color"] = color
    if wireframe is not None: props["wireframe"] = wireframe
    if opacity is not None: props["opacity"] = opacity
    state.upsert_element(element_id, props=props)
    gui_server.manager.send(el["window_id"], {"type": "update_element", "element_id": element_id, "props": props})
    return {"ok": True}


# ---------------------------------------------------------------------
# Exempel: så här registrerar du en variabel Bob får läsa/ändra.
# Kopiera mönstret nedan för egna variabler (t.ex. GPU-läge, volym, etc).
# ---------------------------------------------------------------------
_theme = {"color": "#00eaff"}

variable(
    "hologram_color",
    "Standardfärgen för hologram-GUI:t (hex-sträng).",
    readable=True, writable=True,
    getter=lambda: _theme["color"],
    setter=lambda v: _theme.update(color=v),
)
