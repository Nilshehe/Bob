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

Varje verktyg nedan är ett riktigt LangChain-verktyg (@tool från
langchain_core.tools, med parse_docstring=True så att docstringens
Args-sektion blir per-parameter-beskrivningar i verktygsschemat).
Lägg till en ny funktion, dekorera med @tool(parse_docstring=True),
skriv en Args-rad för varje parameter - så får Bob den automatiskt
tillgänglig via gui.backend.bob_integration.get_langchain_tools(),
utan att du behöver röra något annat i systemet.

Variabler (saker Bob kan läsa/skriva som inte är ett "anrop", t.ex.
Voice Mode eller hologram_color) hanteras fortfarande av den enkla
ToolRegistry-variabeldelen i registry.py - se längst ner i den här
filen.
"""

import uuid
from pathlib import Path
from typing import List, Optional
from typing_extensions import Literal
from urllib.parse import urlparse

from langchain_core.tools import tool

from gui.backend.registry import ToolRegistry, variable
from gui.backend.state_manager import state
import gui.backend.gui_server as gui_server
import gui.backend.window_manager as wm
from gui.backend import html_sanitizer, html_components, theme


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


def _send_update_element(window_id, element_id):
    """
    Skicka en uppdatering av ett redan skapat element (props/w/h/label)
    till ett fönster - t.ex. när create_config_widget återanvänder en
    redan öppen settings-widget istället för att skapa en ny.
    """

    payload = {
        "type": "update_element",
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


@tool(parse_docstring=True)
def create_config_widget(
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 420,
    h: int = 700,
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en widget med Bobs konfiguration (skrollbar).

    Visar TOOLS/APPROVAL som toggles, en auto-genererad SETTINGS-sektion
    för alla övriga skalära värden i config.json (temperature, num_ctx,
    system_prompt, TALKING, VOICE_MODE, m.m. - nya nycklar i config.json
    dyker upp här automatiskt, ingen kodändring behövs), samt en
    MODEL-sektion: välj provider ("ollama" ger en lista med lokalt
    installerade modeller; en API-provider ger fri text för modellnamn,
    ett fält för vilken .env-variabel som har API-nyckeln, en
    OK/saknas-indikator för den nyckeln, och en "testa om modellen
    finns"-knapp), samt AI PER AGENT: Approval/Edit/Research/Code AI
    kan var och en köras med en egen provider/modell. Apply & Restart-
    knappen startar om agenten med den nya konfigurationen.

    Widgeten är ett helt vanligt element (flyttbar/borttagbar) - använd
    move_settings_widget/remove_settings_widget för att slippa hålla
    reda på dess element_id själv.

    Om fönstret redan har en öppen settings-widget återanvänds den
    (position/storlek och config-innehåll uppdateras, ingen ny widget
    skapas) - annars skapas en ny som förr. Det förhindrar att flera
    settings-widgetar staplas ovanpå varandra på samma default-position
    så att bara den senast skapade syns/går att klicka på.

    Args:
        window_id: Fönstret där widgeten ska placeras.
        x: X-position.
        y: Y-position.
        w: Bredd.
        h: Höjd.
        element_id: Valfritt element-id.
    """
    from config_manager import (
        load_config, get_ollama_models, has_api_key,
        get_all_configured_providers, get_chatterbox_voices,
    )

    config = load_config()
    models = get_ollama_models()
    provider = config.get("provider", "ollama")

    # Återanvänd en redan öppen settings-widget i samma fönster istället
    # för att stapla en ny ovanpå den (annars visas/nås bara den senast
    # skapade - se _find_config_widgets).
    existing = _find_config_widgets(window_id)
    reused = bool(existing) and element_id is None
    if existing and element_id is None:
        element_id = existing[0]["element_id"]
    element_id = (
        element_id
        or f"config_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="config_widget",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label="Bob Configuration",
        visible=True,
        props={
            "config": config,
            "models": models,
            "chatterbox_voices": get_chatterbox_voices(),
            "has_api_key": has_api_key(provider),
            "has_api_key_by_provider": {
                p: has_api_key(p) for p in get_all_configured_providers()
            },
        }
    )

    if reused:
        _send_update_element(window_id, element_id)
    else:
        _send_create_element(
            window_id,
            element_id,
        )

    return {
        "ok": True,
        "element_id": element_id,
        "reused_existing": reused,
    }


@tool(parse_docstring=True)
def create_element(
    element_type: Literal[
        "text", "button", "panel", "status", "input", "3d", "toggle", "progress",
    ],
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 200,
    h: int = 80,
    label: str = "",
    element_id: Optional[str] = None,
) -> dict:
    """Skapa ett nytt GUI-element (text, button, panel, status, input, 3d
    eller toggle) i ett fönster. För toggle-knappar som visar/ändrar en
    variabel, använd hellre create_toggle_button.

    Args:
        element_type: Typ av element.
        window_id: Vilket fönster elementet ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Text/etikett på elementet.
        element_id: Valfritt eget id, annars genereras ett.
    """
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


@tool(parse_docstring=True)
def create_agent_monitor(
    agent: Literal["code_ai", "research_ai", "edit_ai"],
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 320,
    h: int = 240,
    label: Optional[str] = None,
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en live-monitor för Code AI, Research AI eller Edit AI.
    Monitorn visar status, aktuell aktivitet, progress, verktyg och job_id.

    Args:
        agent: Vilken AI som ska övervakas.
        window_id: Vilket fönster monitorn ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett ovanför monitorn, annars används agentens namn.
        element_id: Valfritt eget id, annars genereras ett.
    """
    if agent not in {
        "code_ai",
        "research_ai",
        "edit_ai",
    }:
        raise ValueError(
            f"Okänd agent: {agent}"
        )

    names = {
        "code_ai": "CODE AI",
        "research_ai": "RESEARCH AI",
        "edit_ai": "EDIT AI",
    }

    element_id = (
        element_id
        or f"agent_monitor_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="agent_monitor",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label or names[agent],
        visible=True,
        props={
            "agent": agent,
            "status": "IDLE",
            "activity": "Waiting...",
            "progress": 0,
            "tool": "",
            "job_id": "",
            "step": 0,
        },
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "ok": True,
        "element_id": element_id,
        "agent": agent,
    }


@tool(parse_docstring=True)
def create_toggle_button(
    variable_name: str,
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 160,
    h: int = 60,
    label: Optional[str] = None,
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en knapp som visar aktuellt värde för en registrerad variabel
    (t.ex. 'Voice Mode') och byter värdet (true/false) varje gång
    användaren klickar på den. Variabeln måste vara läsbar och skrivbar
    (se list_variables/systemprompten för vilka som finns).

    Args:
        variable_name: Namnet på en registrerad variabel, t.ex. 'Voice Mode'.
        window_id: Vilket fönster knappen ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett ovanför knappen, annars används variabelns namn.
        element_id: Valfritt eget id, annars genereras ett.
    """
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


@tool(parse_docstring=True)
def set_progress(element_id: str, value: int) -> dict:
    """Sätt värdet (0-100) på en progressbar-widget.

    Args:
        element_id: ID för progressbar-elementet.
        value: Nytt värde, 0-100.
    """
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


@tool(parse_docstring=True)
def remove_element(element_id: str, permanent: bool = False) -> dict:
    """Ta bort eller dölj ett GUI-element. permanent=False döljer det bara
    (det finns kvar och kan visas igen), permanent=True raderar det helt.

    Args:
        element_id: ID för elementet som ska tas bort/döljas.
        permanent: True = radera helt, False = bara dölj.
    """
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


@tool(parse_docstring=True)
def show_element(element_id: str) -> dict:
    """Visa ett tidigare dolt element igen.

    Args:
        element_id: ID för elementet som ska visas.
    """
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


def _find_config_widgets(window_id: Optional[str] = None) -> list[dict]:
    """Hittar alla config_widget-element (Bobs settings-widget), i ett
    specifikt fönster om window_id anges, annars i alla fönster."""
    elements = (
        state.all_elements_for_window(window_id)
        if window_id
        else state.all_elements()
    )
    return [
        {"element_id": eid, "window_id": e.get("window_id")}
        for eid, e in elements.items()
        if e.get("type") == "config_widget"
    ]


@tool(parse_docstring=True)
def move_settings_widget(x: int, y: int, window_id: Optional[str] = None) -> dict:
    """Flytta Bobs settings-widget (config_widget) till en ny position,
    utan att behöva känna till dess element_id i förväg - slår upp den
    åt dig. Om det finns fler än en öppen (i olika fönster) måste du
    ange window_id för att välja vilken.

    Args:
        x: Ny X-position i pixlar.
        y: Ny Y-position i pixlar.
        window_id: Valfritt - vilket fönsters settings-widget som ska
            flyttas, om det finns fler än en öppen.
    """
    matches = _find_config_widgets(window_id)

    if not matches:
        return {"ok": False, "message": "Ingen settings-widget hittad (skapa en med create_config_widget)."}

    if len(matches) > 1:
        return {
            "ok": False,
            "message": "Flera settings-widgetar öppna - ange window_id.",
            "widgets": matches,
        }

    return move_element.func(matches[0]["element_id"], x, y)


@tool(parse_docstring=True)
def remove_settings_widget(permanent: bool = True, window_id: Optional[str] = None) -> dict:
    """Ta bort (eller dölj) Bobs settings-widget (config_widget), utan
    att behöva känna till dess element_id i förväg - slår upp den åt
    dig. Om det finns fler än en öppen (i olika fönster) måste du ange
    window_id för att välja vilken.

    Args:
        permanent: True = radera helt (default), False = bara dölj
            (kan visas igen med show_element).
        window_id: Valfritt - vilket fönsters settings-widget som ska
            tas bort, om det finns fler än en öppen.
    """
    matches = _find_config_widgets(window_id)

    if not matches:
        return {"ok": False, "message": "Ingen settings-widget hittad."}

    if len(matches) > 1:
        return {
            "ok": False,
            "message": "Flera settings-widgetar öppna - ange window_id.",
            "widgets": matches,
        }

    return remove_element.func(matches[0]["element_id"], permanent)


@tool(parse_docstring=True)
def move_element(element_id: str, x: int, y: int) -> dict:
    """Flytta ett befintligt GUI-element till en ny position.

    Args:
        element_id: ID för elementet som ska flyttas.
        x: Ny X-position i pixlar.
        y: Ny Y-position i pixlar.
    """
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


@tool(parse_docstring=True)
def move_element_to_window(
    element_id: str,
    window_id: str,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> dict:
    """Flytta ett GUI-element från ett fönster till ett annat. Widgeten
    behåller sin storlek, typ, innehåll och egenskaper.

    Args:
        element_id: ID för widgeten som ska flyttas.
        window_id: ID för det nya fönstret.
        x: Ny X-position i det nya fönstret.
        y: Ny Y-position i det nya fönstret.
    """
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
            return move_element.func(
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


@tool(parse_docstring=True)
def update_element(
    element_id: str,
    w: Optional[int] = None,
    h: Optional[int] = None,
    label: Optional[str] = None,
    visible: Optional[bool] = None,
    props: Optional[dict] = None,
) -> dict:
    """Uppdatera egenskaper hos ett element: storlek, text/etikett,
    synlighet eller fria extra-egenskaper.

    Args:
        element_id: ID för elementet som ska uppdateras.
        w: Ny bredd i pixlar.
        h: Ny höjd i pixlar.
        label: Ny text/etikett.
        visible: Ny synlighet.
        props: Fria extra-egenskaper som ska mergas in.
    """
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
# HTML-element (generellt GUI-format, GUI-specen punkt 1-10, 55-60)
# ---------------------------------------------------------------------
# HTML är en elementtyp precis som text/button/panel osv - samma state
# (state_manager.py), samma WebSocket-events (create_element/
# update_element/remove_element/element_moved/element_resized) och samma
# persistence. remove_element/show_element/move_element/update_element
# ovan fungerar redan på html-element utan ändringar eftersom de bara
# jobbar mot element_id, inte elementtyp.

@tool(parse_docstring=True)
def create_html(
    window_id: str,
    html: str,
    x: int = 40,
    y: int = 40,
    w: int = 320,
    h: int = 200,
    label: str = "",
    element_id: Optional[str] = None,
    raw: bool = False,
) -> dict:
    """Skapa ett eget HTML-GUI-element. Du får kombinera div/span/h1-h6/p/
    button/input/textarea/select/table/img/video/canvas m.fl. säkra
    HTML-taggar, plus egen CSS i style="". <script>, <iframe>, <object>,
    <embed> och alla onclick-liknande attribut tas bort automatiskt -
    interaktion (t.ex. knappar) går via data-bob-action="namn" (och
    valfritt data-bob-value="..."), som skickas tillbaka till dig som ett
    html_action-event. Neutrala färger (t.ex. background:white;
    color:black) tonas automatiskt i Bobs tema (gråskala + temafärg) om
    du inte sätter raw=True - använd raw=True för foton eller annat där
    originalfärgerna är viktiga.

    Args:
        window_id: Vilket fönster elementet ska skapas i.
        html: HTML-innehållet. Saneras automatiskt innan det visas.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett i widgetens header.
        element_id: Valfritt eget id, annars genereras ett.
        raw: True = visa i originalfärger, ingen automatisk temafärgning.
    """
    element_id = (
        element_id
        or f"html_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="html",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label,
        visible=True,
        html=html_sanitizer.sanitize_html(html),
        component=None,
        props={"raw": raw},
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {
        "element_id": element_id
    }


@tool(parse_docstring=True)
def update_html(
    element_id: str,
    html: Optional[str] = None,
    component: Optional[str] = None,
    props: Optional[dict] = None,
    label: Optional[str] = None,
) -> dict:
    """Uppdatera ett HTML-element: byt HTML-innehåll, byt till en färdig
    komponentmall, mergea in nya props, eller byt etikett - utan att
    ändra position eller storlek (använd update_element för det, det
    fungerar på html-element precis som på alla andra elementtyper).

    Args:
        element_id: ID för HTML-elementet som ska uppdateras.
        html: Ny fri HTML (saneras automatiskt). Ignoreras om component anges.
        component: Byt till en färdig komponentmall, se create_html_component.
        props: Fria props som mergas in i elementets nuvarande props.
        label: Ny etikett.
    """
    el = state.get_element(element_id)

    if not el or el.get("type") != "html":
        raise ValueError(f"Okänt HTML-element: {element_id}")

    merged_props = {**el.get("props", {}), **(props or {})}
    fields = {"props": merged_props}

    if label is not None:
        fields["label"] = label

    if component is not None:
        if component not in html_components.COMPONENTS:
            raise ValueError(f"Okänd komponent: {component}")
        rendered, _w, _h = html_components.COMPONENTS[component](merged_props)
        fields["component"] = component
        fields["html"] = rendered
    elif html is not None:
        fields["component"] = None
        fields["html"] = html_sanitizer.sanitize_html(html)

    state.upsert_element(element_id, **fields)

    gui_server.manager.send(
        el["window_id"],
        {
            "type": "update_element",
            "element_id": element_id,
            **fields,
        },
    )

    return {"ok": True}


@tool(parse_docstring=True)
def create_html_component(
    component: Literal[
        "text", "panel", "status", "button", "input", "toggle", "progress",
        "image", "video", "camera_feed", "browser",
    ],
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: Optional[int] = None,
    h: Optional[int] = None,
    label: Optional[str] = None,
    element_id: Optional[str] = None,
    props: Optional[dict] = None,
) -> dict:
    """Skapa ett HTML-element från en färdig mall istället för att skriva
    all HTML själv. Stödda mallar: text, panel, status, button, input,
    toggle, progress, image, video, camera_feed, browser.

    toggle kräver props={"variable": "..."} (namn på en registrerad
    variabel, se list_variables) - nuvarande värde läses in automatiskt.
    image/video tar props={"src": "..."}; sätt props={"raw": true} för
    att visa originalfärger istället för Bobs temafärg (se create_html).
    camera_feed tar antingen props={"source": "local"} (användarens egen
    kamera via webbläsarens getUserMedia) eller props={"source": "x",
    "url": "..."} för en extern kamera/MJPEG-ström.
    browser tar props={"url": "...", "show_address_bar": true/false}.

    Args:
        component: Vilken färdig mall som ska användas.
        window_id: Vilket fönster elementet ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar, annars mallens standardbredd.
        h: Höjd i pixlar, annars mallens standardhöjd.
        label: Etikett i widgetens header, annars komponentnamnet.
        element_id: Valfritt eget id, annars genereras ett.
        props: Props mallen tar, se beskrivningen ovan per komponent.
    """
    if component not in html_components.COMPONENTS:
        raise ValueError(f"Okänd komponent: {component}")

    props = dict(props or {})

    # toggle: läs in variabelns nuvarande värde automatiskt (punkt 11/41)
    if component == "toggle" and "variable" in props and "value" not in props:
        props["value"] = bool(ToolRegistry.get_variable(props["variable"]))

    rendered, default_w, default_h = html_components.COMPONENTS[component](props)

    element_id = (
        element_id
        or f"{component}_{uuid.uuid4().hex[:6]}"
    )

    state.upsert_element(
        element_id,
        type="html",
        window_id=window_id,
        x=x,
        y=y,
        w=w or default_w,
        h=h or default_h,
        label=label or component,
        visible=True,
        html=rendered,
        component=component,
        props=props,
    )

    _send_create_element(
        window_id,
        element_id,
    )

    return {"element_id": element_id}


@tool(parse_docstring=True)
def capture_camera_frame(element_id: str) -> dict:
    """(Inte färdigkopplad än) Ska ta en enstaka bild-snapshot från en
    camera_feed-widgets ström och skicka den till dig. Kräver en
    async request/svar-runda över WebSocket (ett nytt
    "capture_camera_frame"/"camera_frame_captured"-meddelandepar i
    gui_server.py + app.js) som inte ingår i den här patchen ännu.

    Args:
        element_id: ID för camera_feed-elementet.
    """
    raise NotImplementedError(
        "capture_camera_frame är inte kopplat till frontend än - se "
        "docstringen för vad som behöver byggas."
    )


# ---------------------------------------------------------------------
# Theme (GUI-specen punkt 24-30, 59-60)
# ---------------------------------------------------------------------

@tool(parse_docstring=True)
def set_theme_color(accent: str) -> dict:
    """Byt Bobs huvudtemafärg (accent). Övriga temafärger (bakgrund, yta,
    text, muted) räknas om automatiskt utifrån den. Alla HTML-widgetar
    (inklusive gråskale-tonad media) och de befintliga widget-typerna
    uppdateras direkt i alla öppna fönster utan att skapas om.

    Args:
        accent: Hex-färg, t.ex. "#00eaff" eller "#ff6600".
    """
    new_theme = theme.set_accent(accent)

    gui_server.manager.broadcast({
        "type": "theme_state",
        **new_theme,
    })

    return new_theme


@tool(parse_docstring=True)
def get_theme_state() -> dict:
    """Läs av Bobs aktuella tema: accentfärg och de färger som räknas
    fram från den (bakgrund, yta, text, muted, samt de fasta semantiska
    färgerna error/warning/success/info)."""
    return theme.get_theme()


# ---------------------------------------------------------------------
# Fönster
# ---------------------------------------------------------------------

@tool(parse_docstring=True)
def create_window(
    title: str = "Bob",
    width: int = 900,
    height: int = 600,
    screen: Optional[int] = None,
) -> dict:
    """Skapa ett nytt GUI-fönster, ev. på en specifik skärm.

    Args:
        title: Fönstertitel.
        width: Bredd i pixlar.
        height: Höjd i pixlar.
        screen: Skärmindex från get_screens().
    """
    window_id = wm.create_window(
        title=title,
        width=width,
        height=height,
        screen=screen,
    )

    gui_server.broadcast_windows_list()

    return {
        "window_id": window_id
    }


@tool(parse_docstring=True)
def move_window(
    window_id: str,
    x: Optional[int] = None,
    y: Optional[int] = None,
    screen: Optional[int] = None,
) -> dict:
    """Flytta ett fönster till nya koordinater eller till en annan skärm.

    Args:
        window_id: ID för fönstret som ska flyttas.
        x: Ny X-position.
        y: Ny Y-position.
        screen: Skärmindex, t.ex. 0 eller 1.
    """
    wm.move_window(
        window_id,
        x=x,
        y=y,
        screen=screen,
    )

    return {
        "ok": True
    }


@tool(parse_docstring=True)
def close_window(window_id: str) -> dict:
    """Stäng ett GUI-fönster (och de element som hör till det). Säker
    att anropa även om window_id redan är stängt/aldrig fanns - då
    returneras bara ok=False istället för att kasta ett fel.

    Args:
        window_id: ID för fönstret som ska stängas.
    """
    existed = any(w["window_id"] == window_id for w in wm.get_windows())

    wm.close_window(
        window_id
    )

    gui_server.broadcast_windows_list()

    return {
        "ok": existed,
        "message": "Closed." if existed else f"No such window: {window_id} (already closed or never existed).",
    }


@tool(parse_docstring=True)
def get_screens() -> list:
    """Lista anslutna skärmar med upplösning och position."""
    return wm.get_screens()


@tool(parse_docstring=True)
def list_windows() -> list:
    """Lista alla öppna GUI-fönster med window_id, titel, position,
    storlek och antal element. Använd innan element skapas/flyttas för
    att se vilka window_id som faktiskt finns."""
    return wm.get_windows()


@tool(parse_docstring=True)
def list_widgets(window_id: Optional[str] = None) -> list:
    """Lista alla widgetar/element (knappar, config_widget,
    text-paneler, 3d-modeller, etc) i ett fönster, eller i alla fönster
    om window_id inte anges. Använd den här - inte list_windows - för
    att se vilka faktiska widgetar som finns och deras id/typ/position,
    t.ex. innan du flyttar, döljer eller tar bort ett element.

    Args:
        window_id: Valfritt - begränsa listan till ett specifikt
            fönster. Utelämnas för att lista widgetar i alla fönster.
    """
    elements = (
        state.all_elements_for_window(window_id)
        if window_id
        else state.all_elements()
    )

    return [
        {
            "element_id": eid,
            "type": e.get("type"),
            "window_id": e.get("window_id"),
            "label": e.get("label"),
            "x": e.get("x"),
            "y": e.get("y"),
            "w": e.get("w"),
            "h": e.get("h"),
            "visible": e.get("visible", True),
        }
        for eid, e in elements.items()
    ]


# ---------------------------------------------------------------------
# 3D
# ---------------------------------------------------------------------

@tool(parse_docstring=True)
def load_3d_model(
    model_path: str,
    window_id: str,
    element_id: Optional[str] = None,
    x: int = 200,
    y: int = 100,
) -> dict:
    """Ladda och rendera en 3D-modell (glb/gltf) i hologramstil i ett
    fönster. Om model_path bara är ett filnamn söks modellen automatiskt
    i gui/frontend/models/.

    Args:
        model_path: Filnamn, sökväg eller URL till 3D-modellen.
        window_id: Vilket fönster modellen ska visas i.
        element_id: Valfritt eget id, annars genereras ett.
        x: X-position i pixlar.
        y: Y-position i pixlar.
    """
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


@tool(parse_docstring=True)
def move_3d_model(element_id: str, x: float, y: float, z: float) -> dict:
    """Flytta en redan laddad 3D-modell inuti sin egen scen
    (3D-koordinater, inte skärm-koordinater).

    Args:
        element_id: ID för 3D-elementet.
        x: Ny X-position i 3D-scenen.
        y: Ny Y-position i 3D-scenen.
        z: Ny Z-position i 3D-scenen.
    """
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


@tool(parse_docstring=True)
def set_3d_model_style(
    element_id: str,
    color: Optional[str] = None,
    wireframe: Optional[bool] = None,
    opacity: Optional[float] = None,
) -> dict:
    """Ändra färg, wireframe-läge eller genomskinlighet på en laddad
    3D-modell.

    Args:
        element_id: ID för 3D-elementet.
        color: Hex-färg, t.ex. '#00eaff'.
        wireframe: True/False för wireframe-läge.
        opacity: Genomskinlighet, 0.0-1.0.
    """
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


# ---------------------------------------------------------------------
# Svarswidget (stream panel)
# ---------------------------------------------------------------------
# Den permanenta panelen som visar Bobs svarsström live (text, resonemang,
# tool calls, interrupts). Till skillnad från vanliga GUI-element är den
# inte en dynamisk widget i "elements"-listan - den är fast UI i frontend,
# men styrs på samma sätt: via websocket-meddelanden + persistent state.

@tool(parse_docstring=True)
def set_stream_panel(
    visible: Optional[bool] = None,
    tab_hidden: Optional[bool] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    w: Optional[int] = None,
    h: Optional[int] = None,
    windows: Optional[list] = None,
) -> dict:
    """Visa, göm, flytta, ändra storlek på eller välj vilka fönster som
    ska visa Bobs live-svarswidget (panelen som visar
    text/resonemang/tool calls/interrupts i realtid, normalt uppe till
    höger). Ange bara de fält som ska ändras.

    Args:
        visible: True = visa panelen, False = göm den.
        tab_hidden: True = göm även den lilla "◈ live"-fliken som
            annars alltid ligger kvar synlig som väg tillbaka in när
            panelen är gömd. False = visa fliken igen. Använd med
            försiktighet - enda vägen tillbaka i UI:t då är
            snabbkommandot Ctrl+Shift+L eller att sätta det här
            verktyget till False igen.
        x: Ny X-position i pixlar (skärm-koordinater).
        y: Ny Y-position i pixlar.
        w: Ny bredd i pixlar.
        h: Ny höjd i pixlar.
        windows: Lista med window_id för de fönster som ska visa
            live-texten. Tom lista = visa i alla öppna fönster.
    """
    panel = state.update_stream_panel(
        visible=visible,
        tab_hidden=tab_hidden,
        x=x,
        y=y,
        w=w,
        h=h,
        windows=windows,
    )

    gui_server.manager.broadcast({
        "type": "stream_panel_state",
        **panel,
    })

    return panel


@tool(parse_docstring=True)
def set_stream_panel_filters(
    text: Optional[bool] = None,
    reasoning: Optional[bool] = None,
    tool_call_chunk: Optional[bool] = None,
    interrupt: Optional[bool] = None,
) -> dict:
    """Ställ in vilka typer av innehåll Bobs live-svarswidget ska visa.
    Ange bara de fält som ska ändras, resten lämnas som de är.

    Args:
        text: Visa Bobs textsvar.
        reasoning: Visa Bobs resonemang/tankar.
        tool_call_chunk: Visa verktygsanrop Bob gör.
        interrupt: Visa interrupts (godkännande-förfrågningar).
    """
    panel = state.update_stream_panel(
        filters={
            "text": text,
            "reasoning": reasoning,
            "tool_call_chunk": tool_call_chunk,
            "interrupt": interrupt,
        }
    )

    gui_server.manager.broadcast({
        "type": "stream_panel_state",
        **panel,
    })

    return panel


@tool(parse_docstring=True)
def clear_stream_panel() -> dict:
    """Rensa allt innehåll (historik) i Bobs live-svarswidget, utan att
    ändra synlighet, position eller filterinställningar."""
    gui_server.manager.broadcast({
        "type": "stream_panel_clear",
    })

    return {"ok": True}


@tool(parse_docstring=True)
def get_stream_panel_state() -> dict:
    """Läs av aktuellt state för Bobs live-svarswidget: synlighet,
    position, storlek, vilka innehållstyper som visas och vilka fönster
    den visas i just nu."""
    return state.get_stream_panel()


# ---------------------------------------------------------------------
# Diagram / graf / stor text / whiteboard
# ---------------------------------------------------------------------
# Dessa widgets uppdaterar sig själva live: grafen lyssnar på
# "metrics_tick"-broadcasten från funktioner/metrics.py (var 5:e sekund),
# stortexten kan antingen visa ett statiskt värde Bob sätter via
# update_element, eller vara bunden till en registrerad ToolRegistry-
# variabel (t.ex. "Token Usage: main") som pollas i frontend.
# Whiteboarden är tvåvägs: både Bob (via update_element/set_whiteboard_text)
# och användaren (skriver direkt i fönstret) kan ändra den.

@tool(parse_docstring=True)
def create_graph(
    window_id: str,
    series: List[str],
    x: int = 40,
    y: int = 40,
    w: int = 420,
    h: int = 240,
    label: Optional[str] = None,
    interval_seconds: int = 300,
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en live-graf som ritar en eller flera namngivna tidsserier
    över tid (t.ex. "tokens:main", "tokens:code_ai" - alla kända serier
    kan listas med list_metric_series). Grafen uppdateras automatiskt var
    5:e sekund. Användaren kan själv välja tidsintervall i widgeten
    (senaste minuten/5 min/15 min/1 h/allt); interval_seconds sätter bara
    startvärdet.

    Args:
        window_id: Vilket fönster grafen ska skapas i.
        series: Lista med serienamn att rita, t.ex. ["tokens:main"].
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett ovanför grafen.
        interval_seconds: Starttidsintervall i sekunder (t.ex. 300 = 5 min).
        element_id: Valfritt eget id, annars genereras ett.
    """
    element_id = element_id or f"graph_{uuid.uuid4().hex[:6]}"

    history = {s: get_metric_history.func(s) for s in series}

    state.upsert_element(
        element_id,
        type="graph",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label or ("Graf: " + ", ".join(series)),
        visible=True,
        props={
            "series": series,
            "interval_s": interval_seconds,
            "history": history,
        },
    )

    _send_create_element(window_id, element_id)

    return {"element_id": element_id, "series": series}


@tool(parse_docstring=True)
def list_metric_series() -> list:
    """Lista alla kända mätvärdesserier som kan ritas med create_graph
    (t.ex. "tokens:main", "tokens:code_ai")."""
    from funktioner import metrics
    return metrics.list_series()


@tool(parse_docstring=True)
def get_metric_history(series: str, since_seconds_ago: Optional[int] = None) -> list:
    """Läs historiken för en mätvärdesserie som punkter [{t, v}, ...].

    Args:
        series: Serienamn, t.ex. "tokens:main".
        since_seconds_ago: Om satt, returnera bara punkter nyare än så
            här många sekunder tillbaka. Annars hela historiken.
    """
    from funktioner import metrics
    import time
    since = time.time() - since_seconds_ago if since_seconds_ago else None
    return metrics.get_series(series, since=since)


@tool(parse_docstring=True)
def create_big_text(
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 260,
    h: int = 140,
    label: Optional[str] = None,
    text: str = "",
    variable_name: Optional[str] = None,
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en stor text-visare (stor, centrerad text) - bra för ett
    enda viktigt värde, t.ex. en tokenräknare eller status. Kan antingen
    visa statisk text (uppdatera med update_element) eller vara bunden
    till en registrerad variabel (se list_variables) som då pollas och
    uppdateras automatiskt i frontend var 5:e sekund.

    Args:
        window_id: Vilket fönster visaren ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett ovanför texten.
        text: Statiskt starttextvärde (ignoreras om variable_name anges).
        variable_name: Namn på en registrerad variabel att visa live,
            t.ex. "Token Usage: main". Måste vara läsbar.
        element_id: Valfritt eget id, annars genereras ett.
    """
    if variable_name is not None:
        # Kastar ValueError om variabeln inte finns/inte är läsbar - Bob
        # ska se felet direkt istället för en visare som aldrig uppdateras.
        text = str(ToolRegistry.get_variable(variable_name))

    element_id = element_id or f"bigtext_{uuid.uuid4().hex[:6]}"

    state.upsert_element(
        element_id,
        type="big_text",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label or variable_name or "",
        visible=True,
        props={
            "text": text,
            "variable": variable_name,
        },
    )

    _send_create_element(window_id, element_id)

    return {"element_id": element_id, "text": text}


@tool(parse_docstring=True)
def create_whiteboard(
    window_id: str,
    x: int = 40,
    y: int = 40,
    w: int = 320,
    h: int = 220,
    label: Optional[str] = None,
    text: str = "",
    element_id: Optional[str] = None,
) -> dict:
    """Skapa en whiteboard: en fri textyta som både Bob (via
    update_element/set_whiteboard_text) och användaren (skriver direkt i
    fönstret) kan skriva i. Bra för anteckningar, planer eller
    scratchpad-resonemang som ska synas i GUI:t.

    Args:
        window_id: Vilket fönster whiteboarden ska skapas i.
        x: X-position i pixlar.
        y: Y-position i pixlar.
        w: Bredd i pixlar.
        h: Höjd i pixlar.
        label: Etikett ovanför whiteboarden.
        text: Starttext.
        element_id: Valfritt eget id, annars genereras ett.
    """
    element_id = element_id or f"whiteboard_{uuid.uuid4().hex[:6]}"

    state.upsert_element(
        element_id,
        type="whiteboard",
        window_id=window_id,
        x=x,
        y=y,
        w=w,
        h=h,
        label=label or "Whiteboard",
        visible=True,
        props={"text": text},
    )

    _send_create_element(window_id, element_id)

    return {"element_id": element_id}


@tool(parse_docstring=True)
def set_whiteboard_text(element_id: str, text: str) -> dict:
    """Skriv över texten i en whiteboard.

    Args:
        element_id: ID för whiteboard-elementet.
        text: Ny text.
    """
    el = state.get_element(element_id)
    if not el or el.get("type") != "whiteboard":
        raise ValueError(f"Okänd whiteboard: {element_id}")

    props = {**el.get("props", {}), "text": text}
    state.upsert_element(element_id, props=props)

    gui_server.manager.send(
        el["window_id"],
        {"type": "update_element", "element_id": element_id, "props": props},
    )

    return {"ok": True}


# ---------------------------------------------------------------------
# På/av för hela GUI:t
# ---------------------------------------------------------------------
# GUI:t är avstängt som standard när Bob:s process startar (main.py
# startar inte längre launch_gui() automatiskt). Bob slår på/av det
# själv med de här två verktygen. Importerar main_gui lokalt (inte
# module-nivå) eftersom main_gui.py importerar den här filen för att
# registrera verktygen - en import i toppen här skulle bli cirkulär.

@tool(parse_docstring=True)
def start_gui() -> dict:
    """Starta Bobs GUI (öppnar fönstret/fönstren på skärmen). GUI:t är
    AVSTÄNGT som standard när Bob startar - använd det här verktyget när
    du eller användaren vill se/använda det grafiska gränssnittet.
    No-op om GUI:t redan är igång."""
    import gui.backend.main_gui as main_gui

    main_gui.start_gui()
    return {"ok": True, "running": main_gui.is_gui_running()}


@tool(parse_docstring=True)
def stop_gui() -> dict:
    """Stäng av Bobs GUI (stänger alla öppna fönster). Kan startas igen
    senare med start_gui. No-op om GUI:t redan är avstängt."""
    import gui.backend.main_gui as main_gui

    main_gui.stop_gui()
    return {"ok": True, "running": main_gui.is_gui_running()}


@tool(parse_docstring=True)
def get_gui_status() -> dict:
    """Kolla om Bobs GUI är igång eller avstängt just nu, utan att ändra
    något."""
    import gui.backend.main_gui as main_gui

    return {"running": main_gui.is_gui_running()}
