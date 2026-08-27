"""
gui_tools.py
De konkreta GUI-verktyg Bob får använda.

Stöd:
- skapa/ta bort/visa/flytta GUI-element
- flytta element mellan fönster
- skapa/flytta/styla 3D-modeller
- skapa/flytta/stäng fönster
- persistent GUI-state
- 3D-modeller från gui/frontend/models/
"""

import uuid
from pathlib import Path
from urllib.parse import urlparse

from gui.backend.registry import tool, variable
from gui.backend.state_manager import state
import gui.backend.gui_server as gui_server
import gui.backend.window_manager as wm


# ---------------------------------------------------------------------
# 3D-modellmapp
# ---------------------------------------------------------------------

MODELS_DIR = Path(__file__).parent.parent / "frontend" / "models"


# ---------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------

def _wid_for_element(element_id):
    el = state.get_element(element_id)
    return el.get("window_id") if el else None


def _element_payload(element_id):
    """
    Gör om ett state-element till ett WebSocket-payload.

    State använder:
        type = elementtyp

    WebSocket använder:
        type = kommando

    Därför skickar vi elementtypen som:
        element_type
    """

    el = state.get_element(element_id)

    if not el:
        raise ValueError(f"Okänt element: {element_id}")

    return {
        "element_id": element_id,
        "element_type": el.get("type"),
        **{
            key: value
            for key, value in el.items()
            if key != "type"
        },
    }


def _send_create_element(window_id, element_id):
    """
    Skicka ett element till ett fönster.
    """

    payload = {
        "type": "create_element",
        **_element_payload(element_id),
    }

    gui_server.manager.send(
        window_id,
        payload,
    )


def _normalize_model_path(model_path):
    """
    Gör modellvägar enkla för Bob.

    Exempel:

        robot.glb
        -> /static/models/robot.glb

        models/robot.glb
        -> /static/models/robot.glb

        /static/models/robot.glb
        -> lämnas som det är

        https://example.com/robot.glb
        -> lämnas som det är
    """

    if not model_path:
        raise ValueError("model_path får inte vara tom.")

    model_path = str(model_path).strip()

    # URL
    parsed = urlparse(model_path)

    if parsed.scheme in ("http", "https"):
        return model_path

    # Redan en frontend-URL
    if model_path.startswith("/static/"):
        return model_path

    clean = model_path.replace("\\", "/").lstrip("/")

    if clean.startswith("models/"):
        clean = clean[len("models/"):]

    # Förhindra ../-sökvägar
    candidate = (MODELS_DIR / clean).resolve()
    models_root = MODELS_DIR.resolve()

    try:
        candidate.relative_to(models_root)
    except ValueError:
        raise ValueError(
            "3D-modellen måste ligga i gui/frontend/models/ "
            "eller anges som en http/https-URL."
        )

    if not candidate.exists():
        raise FileNotFoundError(
            f"3D-modellen hittades inte: {candidate}\n"
            f"Lägg modellen i: {MODELS_DIR}"
        )

    return "/static/models/" + clean.replace("\\", "/")


# ---------------------------------------------------------------------
# GUI-element
# ---------------------------------------------------------------------

@tool(
    "Skapa ett nytt GUI-element (text, button, panel, status, input, 3d "
    "eller toggle) i ett fönster. För toggle-knappar som visar/ändrar en "
    "variabel, använd hellre create_toggle_button.",
    parameters={
        "element_type": {
            "type": "string",
            "enum": [
                "text",
                "button",
                "panel",
                "status",
                "input",
                "3d",
                "toggle",
                "progress",
            ],
        },
        "window_id": {
            "type": "string",
            "description": "Vilket fönster elementet ska skapas i",
        },
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "w": {"type": "integer"},
        "h": {"type": "integer"},
        "label": {
            "type": "string",
            "description": "Text/etikett på elementet",
        },
        "element_id": {
            "type": "string",
            "description": "Valfritt eget id, annars genereras ett",
        },
    },
    required=["element_type", "window_id"],
)
def create_element(
    element_type,
    window_id,
    x=40,
    y=40,
    w=200,
    h=80,
    label="",
    element_id=None,
):
    element_id = (
        element_id
        or f"{element_type}_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type=element_type,
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label,
        visible=True,
        props={},
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "element_id": element_id
    }


@tool(
    "Skapa en knapp som visar aktuellt värde för en registrerad variabel "
    "(t.ex. 'Voice Mode') och byter värdet (true/false) varje gång "
    "användaren klickar på den. Variabeln måste vara läsbar och skrivbar "
    "(se list_variables/systemprompten för vilka som finns).",
    parameters={
        "variable_name": {
            "type": "string",
            "description": "Namnet på en registrerad variabel, t.ex. 'Voice Mode'",
        },
        "window_id": {
            "type": "string",
            "description": "Vilket fönster knappen ska skapas i",
        },
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "w": {"type": "integer"},
        "h": {"type": "integer"},
        "label": {
            "type": "string",
            "description": "Etikett ovanför knappen, annars används variabelns namn",
        },
        "element_id": {
            "type": "string",
            "description": "Valfritt eget id, annars genereras ett",
        },
    },
    required=["variable_name", "window_id"],
)
def create_toggle_button(
    variable_name,
    window_id,
    x=40,
    y=40,
    w=160,
    h=60,
    label=None,
    element_id=None,
):
    from gui.backend.registry import ToolRegistry

    # Kastar ValueError om variabeln inte finns eller inte är läsbar -
    # låter Bob se felet istället för att skapa en knapp som inte funkar.
    current_value = ToolRegistry.get_variable(variable_name)

    element_id = (
        element_id
        or f"toggle_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="toggle",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label or variable_name,
        visible=True,
        props={
            "variable": variable_name,
            "value": bool(current_value),
        },
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "element_id": element_id,
        "value": bool(current_value),
    }


@tool(
    "Sätt värdet (0-100) på en progressbar-widget.",
    parameters={
        "element_id": {"type": "string"},
        "value": {
            "type": "integer",
            "description": "0-100",
        },
    },
    required=["element_id", "value"],
)
def set_progress(element_id, value):
    el = state.get_element(element_id)

    if not el:
        raise ValueError(f"Okänt element: {element_id}")

    value = max(0, min(100, value))

    props = {
        **el.get("props", {}),
        "value": value,
    }

    state.upsert_element(
        element_id,
        props=props,
    )

    gui_server.manager.send(
        el["window_id"],
        {
            "type": "update_element",
            "element_id": element_id,
            "props": props,
        },
    )

    return {
        "ok": True,
        "value": value,
    }


@tool(
    "Ta bort eller dölj ett GUI-element. permanent=False döljer det bara "
    "(det finns kvar och kan visas igen), permanent=True raderar det helt.",
    parameters={
        "element_id": {"type": "string"},
        "permanent": {"type": "boolean"},
    },
    required=["element_id"],
)
def remove_element(
    element_id,
    permanent=False,
):
    window_id = _wid_for_element(
        element_id
    )

    if not window_id:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    if permanent:
        state.remove_element(
            element_id
        )
    else:
        state.upsert_element(
            element_id,
            visible=False,
        )

    gui_server.manager.send(
        window_id,
        {
            "type": "remove_element",
            "element_id": element_id,
            "permanent": permanent,
        },
    )

    return {
        "ok": True
    }


@tool(
    "Visa ett tidigare dolt element igen.",
    parameters={
        "element_id": {"type": "string"}
    },
    required=["element_id"],
)
def show_element(element_id):
    el = state.get_element(
        element_id
    )

    if not el:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    state.upsert_element(
        element_id,
        visible=True,
    )

    _send_create_element(
        el["window_id"],
        element_id,
    )

    return {
        "ok": True
    }


@tool(
    "Flytta ett befintligt GUI-element till en ny position.",
    parameters={
        "element_id": {"type": "string"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
    },
    required=[
        "element_id",
        "x",
        "y",
    ],
)
def move_element(
    element_id,
    x,
    y,
):
    window_id = _wid_for_element(
        element_id
    )

    if not window_id:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    state.upsert_element(
        element_id,
        x=x,
        y=y,
    )

    gui_server.manager.send(
        window_id,
        {
            "type": "move_element",
            "element_id": element_id,
            "x": x,
            "y": y,
        },
    )

    return {
        "ok": True
    }


@tool(
    "Flytta ett GUI-element från ett fönster till ett annat. "
    "Widgeten behåller sin storlek, typ, innehåll och egenskaper.",
    parameters={
        "element_id": {
            "type": "string",
            "description": "ID för widgeten som ska flyttas",
        },
        "window_id": {
            "type": "string",
            "description": "ID för det nya fönstret",
        },
        "x": {
            "type": "integer",
            "description": "Ny X-position i det nya fönstret",
        },
        "y": {
            "type": "integer",
            "description": "Ny Y-position i det nya fönstret",
        },
    },
    required=[
        "element_id",
        "window_id",
    ],
)
def move_element_to_window(
    element_id,
    window_id,
    x=None,
    y=None,
):
    el = state.get_element(
        element_id
    )

    if not el:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    old_window_id = el.get(
        "window_id"
    )

    # Samma fönster
    if old_window_id == window_id:

        if x is not None or y is not None:
            return move_element(
                element_id,
                x if x is not None else el.get("x", 40),
                y if y is not None else el.get("y", 40),
            )

        return {
            "ok": True,
            "window_id": window_id,
        }

    new_x = (
        x
        if x is not None
        else el.get("x", 40)
    )

    new_y = (
        y
        if y is not None
        else el.get("y", 40)
    )

    # Ta bort widgeten från gamla fönstret
    if old_window_id:
        gui_server.manager.send(
            old_window_id,
            {
                "type": "remove_element",
                "element_id": element_id,
                "permanent": False,
            },
        )

    # Uppdatera persistent state
    state.upsert_element(
        element_id,
        window_id=window_id,
        x=new_x,
        y=new_y,
        visible=True,
    )

    # Skicka widgeten till nya fönstret
    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "ok": True,
        "element_id": element_id,
        "old_window_id": old_window_id,
        "window_id": window_id,
        "x": new_x,
        "y": new_y,
    }


@tool(
    "Uppdatera egenskaper hos ett element: storlek, text/etikett, synlighet "
    "eller fria extra-egenskaper.",
    parameters={
        "element_id": {"type": "string"},
        "w": {"type": "integer"},
        "h": {"type": "integer"},
        "label": {"type": "string"},
        "visible": {"type": "boolean"},
        "props": {
            "type": "object",
            "description": "Fria extra-egenskaper",
        },
    },
    required=["element_id"],
)
def update_element(
    element_id,
    w=None,
    h=None,
    label=None,
    visible=None,
    props=None,
):
    if not state.get_element(
        element_id
    ):
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    fields = {}

    if w is not None:
        fields["w"] = w

    if h is not None:
        fields["h"] = h

    if label is not None:
        fields["label"] = label

    if visible is not None:
        fields["visible"] = visible

    if props:
        current = (
            state.get_element(
                element_id
            )
            or {}
        )

        fields["props"] = {
            **current.get(
                "props",
                {}
            ),
            **props,
        }

    state.upsert_element(
        element_id,
        **fields,
    )

    window_id = _wid_for_element(
        element_id
    )

    if window_id:
        gui_server.manager.send(
            window_id,
            {
                "type": "update_element",
                "element_id": element_id,
                **fields,
            },
        )

    return {
        "ok": True
    }


# ---------------------------------------------------------------------
# Fönster
# ---------------------------------------------------------------------

@tool(
    "Skapa ett nytt GUI-fönster, ev. på en specifik skärm.",
    parameters={
        "title": {"type": "string"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "screen": {
            "type": "integer",
            "description": "Skärmindex från get_screens()",
        },
    },
)
def create_window(
    title="Bob",
    width=900,
    height=600,
    screen=None,
):
    window_id = wm.create_window(
        title=title,
        width=width,
        height=height,
        screen=screen,
    )

    return {
        "window_id": window_id
    }


@tool(
    "Flytta ett fönster till nya koordinater eller till en annan skärm.",
    parameters={
        "window_id": {"type": "string"},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "screen": {
            "type": "integer",
            "description": "Skärmindex, t.ex. 0 eller 1",
        },
    },
    required=["window_id"],
)
def move_window(
    window_id,
    x=None,
    y=None,
    screen=None,
):
    wm.move_window(
        window_id,
        x=x,
        y=y,
        screen=screen,
    )

    return {
        "ok": True
    }


@tool(
    "Stäng ett GUI-fönster (och de element som hör till det).",
    parameters={
        "window_id": {"type": "string"}
    },
    required=["window_id"],
)
def close_window(window_id):
    wm.close_window(
        window_id
    )

    return {
        "ok": True
    }


@tool(
    "Lista anslutna skärmar med upplösning och position."
)
def get_screens():
    return wm.get_screens()


@tool(
    "Lista alla öppna GUI-fönster med window_id, titel, position, "
    "storlek och antal element. Använd innan element skapas/flyttas "
    "för att se vilka window_id som faktiskt finns."
)
def list_windows():
    return wm.get_windows()


# ---------------------------------------------------------------------
# 3D
# ---------------------------------------------------------------------

@tool(
    "Ladda och rendera en 3D-modell (glb/gltf) i hologramstil i ett fönster. "
    "Om model_path bara är ett filnamn söks modellen automatiskt i "
    "gui/frontend/models/.",
    parameters={
        "model_path": {
            "type": "string",
            "description": "Filnamn, sökväg eller URL till 3D-modellen",
        },
        "window_id": {
            "type": "string"
        },
        "element_id": {
            "type": "string"
        },
        "x": {
            "type": "integer"
        },
        "y": {
            "type": "integer"
        },
    },
    required=[
        "model_path",
        "window_id",
    ],
)
def load_3d_model(
    model_path,
    window_id,
    element_id=None,
    x=200,
    y=100,
):
    model_url = _normalize_model_path(
        model_path
    )

    element_id = (
        element_id
        or f"3d_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="3d",
        window_id=window_id,
        x=x,
        y=y,
        w=400,
        h=400,
        visible=True,
        label="3D",
        props={
            "model_path": model_url,
            "color": "#00eaff",
            "wireframe": True,
            "position3d": [0, 0, 0],
            "scale": 1.0,
        },
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "element_id": element_id,
        "model_path": model_url,
    }


@tool(
    "Flytta en redan laddad 3D-modell inuti sin egen scen "
    "(3D-koordinater, inte skärm-koordinater).",
    parameters={
        "element_id": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
    },
    required=[
        "element_id",
        "x",
        "y",
        "z",
    ],
)
def move_3d_model(
    element_id,
    x,
    y,
    z,
):
    el = state.get_element(
        element_id
    )

    if not el:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    props = {
        **el.get("props", {}),
        "position3d": [x, y, z],
    }

    state.upsert_element(
        element_id,
        props=props,
    )

    gui_server.manager.send(
        el["window_id"],
        {
            "type": "update_element",
            "element_id": element_id,
            "props": props,
        },
    )

    return {
        "ok": True
    }


@tool(
    "Ändra färg, wireframe-läge eller genomskinlighet på en laddad 3D-modell.",
    parameters={
        "element_id": {"type": "string"},
        "color": {
            "type": "string",
            "description": "Hex-färg, t.ex. '#00eaff'",
        },
        "wireframe": {
            "type": "boolean"
        },
        "opacity": {
            "type": "number"
        },
    },
    required=["element_id"],
)
def set_3d_model_style(
    element_id,
    color=None,
    wireframe=None,
    opacity=None,
):
    el = state.get_element(
        element_id
    )

    if not el:
        raise ValueError(
            f"Okänt element: {element_id}"
        )

    props = dict(
        el.get(
            "props",
            {}
        )
    )

    if color is not None:
        props["color"] = color

    if wireframe is not None:
        props["wireframe"] = wireframe

    if opacity is not None:
        props["opacity"] = opacity

    state.upsert_element(
        element_id,
        props=props,
    )

    gui_server.manager.send(
        el["window_id"],
        {
            "type": "update_element",
            "element_id": element_id,
            "props": props,
        },
    )

    return {
        "ok": True
    }


# ---------------------------------------------------------------------
# Variabler
# ---------------------------------------------------------------------

_theme = {
    "color": "#00eaff"
}


variable(
    "hologram_color",
    "Standardfärgen för hologram-GUI:t (hex-sträng).",
    readable=True,
    writable=True,
    getter=lambda: _theme["color"],
    setter=lambda v: _theme.update(
        color=v
    ),
)