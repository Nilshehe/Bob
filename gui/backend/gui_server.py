"""
gui_server.py
FastAPI + WebSocket-ryggrad. Varje pywebview-fönster kopplar upp sig hit
över en websocket. Backend-verktygen (gui_tools.py) skickar ner JSON-
"commands" till rätt fönster; frontend skickar upp "events" (t.ex. att
användaren dragit ett element manuellt) så tillståndet hålls i synk.
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config_manager import load_config

from gui.backend.state_manager import state

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI()


class ConnectionManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, window_id: str, ws: WebSocket):
        await ws.accept()
        self.connections[window_id] = ws

    def disconnect(self, window_id: str):
        self.connections.pop(window_id, None)

    async def _send(self, window_id: str, payload: dict):
        ws = self.connections.get(window_id)
        if ws:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))

    def send(self, window_id: str, payload: dict):
        """Trådsäker skicka-funktion, går att anropa från vanliga (icke-async)
        verktygsfunktioner i gui_tools.py."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._send(window_id, payload), self.loop)

    def broadcast(self, payload: dict):
        for wid in list(self.connections.keys()):
            self.send(wid, payload)


manager = ConnectionManager()


def broadcast_agent_stream(payload: dict):
    """Skickar Bobs live-text (agent_stream/"turn"-markörer) bara till de
    fönster som är valda i svarswidgetens fönster-filter
    (state.get_stream_panel()["windows"]). Tom lista = visa i alla
    fönster, precis som innan fönster-filtret fanns."""
    try:
        enabled_windows = state.get_stream_panel().get("windows") or []
    except Exception:
        enabled_windows = []

    if not enabled_windows:
        manager.broadcast(payload)
        return

    for window_id in enabled_windows:
        manager.send(window_id, payload)


def broadcast_windows_list():
    """Skickar aktuell fönsterlista till alla anslutna klienter, så t.ex.
    kryssrutorna för "vilka fönster ska visa live-texten" hålls i synk när
    fönster öppnas/stängs."""
    import gui.backend.window_manager as wm

    try:
        manager.broadcast({"type": "windows_list", "windows": wm.get_windows()})
    except Exception:
        pass


def broadcast_agent_monitor(
    agent,
    job_id,
    status=None,
    activity=None,
    progress=None,
    tool=None,
    step=None,
):
    payload = {
        "type": "agent_monitor_update",
        "agent": agent,
        "job_id": job_id,
    }

    if status is not None:
        payload["status"] = status
    if activity is not None:
        payload["activity"] = activity
    if progress is not None:
        payload["progress"] = progress
    if tool is not None:
        payload["tool"] = tool
    if step is not None:
        payload["step"] = step

    try:
        manager.broadcast(payload)
    except Exception:
        pass


# ---------------------------------------------------------------------
# Bro till Bob:s agent-loop (main.py körs i en egen tråd/event-loop, skild
# från den här FastAPI/uvicorn-loopen). main.py anropar register_bridge_loop
# med sin egen loop så att t.ex. chattmeddelanden från GUI:t kan skickas
# över till event_queue på rätt tråd.
# ---------------------------------------------------------------------
bridge_loop: Optional[asyncio.AbstractEventLoop] = None


def register_bridge_loop(loop: asyncio.AbstractEventLoop):
    global bridge_loop
    bridge_loop = loop


@app.on_event("startup")
async def on_startup():
    manager.loop = asyncio.get_event_loop()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.websocket("/ws/{window_id}")
async def ws_endpoint(websocket: WebSocket, window_id: str):
    await manager.connect(window_id, websocket)
    # Vid anslutning: skicka hela nuvarande tillstånd för fönstret så det
    # renderas rätt direkt (även efter omstart av Bob).
    win = state.state["windows"].get(window_id, {})
    els = state.all_elements_for_window(window_id)
    await websocket.send_text(json.dumps({"type": "sync", "window": win, "elements": els}, ensure_ascii=False))

    # Skicka aktuellt Voice Mode-läge direkt så den permanenta chatt-inputen/
    # cirkeln renderas rätt från start, även efter en omstart av fönstret.
    try:
        from gui.backend.registry import ToolRegistry
        voice_mode = bool(ToolRegistry.get_variable("Voice Mode"))
    except Exception:
        voice_mode = False

    await websocket.send_text(json.dumps(
        {"type": "voice_state", "mode": voice_mode, "awake": False, "listening": False, "level": 0.0},
        ensure_ascii=False,
    ))

    # Skicka aktuellt state för svarswidgeten (synlighet, position, storlek,
    # vilka innehållstyper som visas) så den renderas rätt direkt.
    await websocket.send_text(json.dumps(
        {"type": "stream_panel_state", **state.get_stream_panel()},
        ensure_ascii=False,
    ))

    # Skicka aktuellt tema så CSS-variablerna sätts rätt direkt, även
    # efter en omstart av fönstret (theme.py).
    from gui.backend import theme as theme_module
    await websocket.send_text(json.dumps(
        {"type": "theme_state", **theme_module.get_theme()},
        ensure_ascii=False,
    ))

    # Skicka aktuell fönsterlista så frontend kan rendera kryssrutorna för
    # "vilka fönster ska visa live-texten" i inställningsmenyn.
    try:
        import gui.backend.window_manager as wm
        await websocket.send_text(json.dumps(
            {"type": "windows_list", "windows": wm.get_windows()},
            ensure_ascii=False,
        ))
    except Exception:
        pass

    try:
        while True:
            raw = await websocket.receive_text()
            _handle_event(window_id, json.loads(raw))
    except WebSocketDisconnect:
        manager.disconnect(window_id)


def _handle_event(window_id: str, msg: dict):
    """Frontend -> backend-event, t.ex. att användaren dragit/ändrat storlek
    på ett element för hand."""
    mtype = msg.get("type")
    if mtype == "element_moved":
        state.upsert_element(msg["element_id"], x=msg["x"], y=msg["y"])
    elif mtype == "element_resized":
        state.upsert_element(msg["element_id"], w=msg["w"], h=msg["h"])
    elif mtype == "element_clicked":
        _handle_element_clicked(window_id, msg.get("element_id"))
    elif mtype == "user_chat_message":
        _handle_user_chat_message(msg.get("content", ""))
    elif mtype == "stream_panel_updated":
        panel = state.update_stream_panel(
            filters=msg.get("filters"),
            windows=msg.get("windows"),
            visible=msg.get("visible"),
            tab_hidden=msg.get("tab_hidden"),
            x=msg.get("x"),
            y=msg.get("y"),
            w=msg.get("w"),
            h=msg.get("h"),
        )
        manager.broadcast({"type": "stream_panel_state", **panel})
    elif mtype == "element_text_changed":
        element_id = msg.get("element_id")
        el = state.get_element(element_id)
        if el and el.get("type") == "whiteboard":
            props = {**el.get("props", {}), "text": msg.get("text", "")}
            state.upsert_element(element_id, props=props)
            manager.broadcast({
                "type": "update_element",
                "element_id": element_id,
                "props": props,
                "_origin_window": window_id,
            })
    elif mtype == "html_action":
        _handle_html_action(window_id, msg)


def _handle_user_chat_message(content: str):
    """Meddelande från den permanenta text-inputen i GUI:t. Läggs på Bob:s
    event_queue precis som ett meddelande skrivet i terminalen eller sagt
    via röst, fast trådsäkert eftersom vi kör i uvicorn:s egen event-loop."""
    content = (content or "").strip()
    if not content or bridge_loop is None:
        return

    from funktioner.queue import event_queue

    asyncio.run_coroutine_threadsafe(
        event_queue.put({"type": "user_message", "content": content}),
        bridge_loop,
    )


def _rerender_config_widget(window_id: str, element_id: str, extra_props: dict = None):
    """Läser om config.json (+ ev. ollama-modellista + API-nyckelstatus)
    och skickar de nya props:en till settings-widgeten. Widgeten byggs
    helt av frontend-JS utifrån props (se app.js: renderConfigWidget) -
    det finns ingen server-renderad HTML att uppdatera här, bara props."""
    from config_manager import (
        load_config, get_ollama_models, has_api_key,
        get_all_configured_providers, get_chatterbox_voices,
    )

    el = state.get_element(element_id)
    if not el:
        return

    config = load_config()
    provider = config.get("provider", "ollama")

    props = {
        **el.get("props", {}),
        "config": config,
        "models": get_ollama_models(),
        "chatterbox_voices": get_chatterbox_voices(),
        "has_api_key": has_api_key(provider),
        # En "har API-nyckel?"-flagga per provider som faktiskt
        # används just nu (huvud-AI:n + varje underagent som fått en
        # egen provider) - Approval/Edit/Research/Code AI kan köra en
        # annan provider än huvud-AI:n, så en enda global has_api_key
        # räcker inte längre.
        "has_api_key_by_provider": {
            p: has_api_key(p) for p in get_all_configured_providers()
        },
    }
    if extra_props:
        props.update(extra_props)

    state.upsert_element(element_id, props=props)
    manager.send(window_id, {
        "type": "update_element",
        "element_id": element_id,
        "props": props,
    })


def _handle_config_toggle(window_id: str, element_id: str, config_path: str):
    import time
    from config_manager import get_config_value, set_config_value

    current = bool(get_config_value(config_path, False))
    set_config_value(config_path, not current)
    _rerender_config_widget(window_id, element_id, extra_props={
        "last_saved": {"path": config_path, "ts": time.time()},
    })


def _handle_config_set(window_id: str, element_id: str, config_path: str, value, numeric: bool = False):
    import time
    from config_manager import set_config_value

    if numeric:
        try:
            value = float(value)
            if value.is_integer():
                value = int(value)
        except (TypeError, ValueError):
            return

    set_config_value(config_path, value)

    # Byter man provider/modell/API-nyckelvariabel (huvud-AI:n eller en
    # underagent) är ett tidigare "testa modell"-resultat inte längre
    # relevant - nollställ det så widgeten inte visar ett missvisande
    # gammalt resultat.
    extra = {"last_saved": {"path": config_path, "ts": time.time()}}
    is_provider_or_model = (
        config_path in ("provider", "model")
        or config_path.startswith("api_key_envs.")
        or config_path.startswith("agents.") and config_path.rsplit(".", 1)[-1] in ("provider", "model")
    )
    if is_provider_or_model:
        # agents.<key>.provider/model -> rensa bara den agentens resultat.
        if config_path.startswith("agents."):
            agent_key = config_path.split(".")[1]
            extra["check_results"] = {**_get_check_results(window_id, element_id), agent_key: None}
        else:
            extra["check_result"] = None

    _rerender_config_widget(window_id, element_id, extra_props=extra)


def _get_check_results(window_id: str, element_id: str) -> dict:
    el = state.get_element(element_id)
    return dict((el or {}).get("props", {}).get("check_results") or {})


def _handle_config_check_model(window_id: str, element_id: str, agent: str = None):
    from config_manager import load_config, check_model, get_agent_settings, AGENT_KEYS

    config = load_config()

    if agent and agent != "main" and agent in AGENT_KEYS:
        default_models = {
            "approval": "qwen3:4b",
            "edit_ai": "qwen3:4b",
            "research_ai": "qwen3:4b",
            "code_ai": "qwen3:4b",
        }
        settings = get_agent_settings(agent, default_models.get(agent, "qwen3:4b"))
        provider, model = settings["provider"], settings["model"]
    else:
        agent = "main"
        provider = config.get("provider", "ollama")
        model = config.get("model", "")

    result = check_model(provider, model)
    result_entry = {"provider": provider, "model": model, **result}

    check_results = _get_check_results(window_id, element_id)
    check_results[agent] = result_entry

    extra = {"check_results": check_results}
    if agent == "main":
        extra["check_result"] = result_entry

    _rerender_config_widget(window_id, element_id, extra_props=extra)


def _handle_config_close(window_id: str, element_id: str):
    """Stänger settings-widgeten direkt från GUI:t (X-knappen), utan att
    Bob behöver anropa remove_element själv."""
    if not element_id:
        return
    state.remove_element(element_id)
    manager.send(window_id, {
        "type": "remove_element",
        "element_id": element_id,
        "permanent": True,
    })


def _handle_html_action(window_id: str, msg: dict):
    """data-bob-action-events från HTML-widgetar (GUI-specen punkt 10)
    OCH från settings-widgeten (element-typen "config_widget", som
    byggs av frontend-JS utifrån props istället för server-HTML).

    Specialfall som hanteras direkt i backend, utan att Bob behöver
    reagera själv: html-toggle (component="toggle"), browser-widgetens
    adressfält, och alla config_*-actions från settings-widgeten.
    Alla events läggs ändå på Bobs event_queue som ett "html_action"
    (utom restart, som är en egen händelsetyp main.py redan lyssnar på)
    så Bob kan reagera om han vill."""
    element_id = msg.get("element_id")
    action = msg.get("action")
    value = msg.get("value")
    config_path = msg.get("config_path")

    if action == "config_toggle" and config_path:
        _handle_config_toggle(window_id, element_id, config_path)
        return

    if action in ("config_text", "config_number") and config_path:
        _handle_config_set(window_id, element_id, config_path, value, numeric=(action == "config_number"))
        return

    if action == "config_provider":
        _handle_config_set(window_id, element_id, config_path or "provider", value)
        return

    if action == "config_model":
        model = msg.get("model") or value
        if model is not None:
            _handle_config_set(window_id, element_id, config_path or "model", model)
        return

    if action == "config_check_model":
        _handle_config_check_model(window_id, element_id, agent=msg.get("agent"))
        return

    if action == "config_close":
        _handle_config_close(window_id, element_id)
        return

    if action == "config_restart":
        if bridge_loop is not None:
            from funktioner.queue import event_queue
            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "restart_agent"}),
                bridge_loop,
            )
        return

    el = state.get_element(element_id) if element_id else None

    if el and el.get("type") == "html" and el.get("component") == "browser" and action == "browser_navigate":
        # Adressfältet i browser-widgeten (GUI-specen: html_components.py
        # _browser) - låt användaren navigera direkt utan att Bob behöver
        # reagera på varje inskriven URL för att det ska funka.
        url = (value or "").strip()
        if url:
            if "://" not in url:
                url = "https://" + url
            from gui.backend import html_components

            props = {**el.get("props", {}), "url": url}
            rendered, _w, _h = html_components.COMPONENTS["browser"](props)
            state.upsert_element(element_id, props=props, html=rendered)
            manager.send(window_id, {
                "type": "update_element",
                "element_id": element_id,
                "props": props,
                "html": rendered,
            })
            value = url

    if el and el.get("type") == "html" and el.get("component") == "toggle" and action == "toggle":
        var_name = el.get("props", {}).get("variable")
        if var_name:
            from gui.backend.registry import ToolRegistry
            try:
                new_value = not bool(ToolRegistry.get_variable(var_name))
                ToolRegistry.set_variable(var_name, new_value)
                import gui.backend.html_components as html_components
                props = {**el.get("props", {}), "value": new_value}
                rendered, _w, _h = html_components.COMPONENTS["toggle"](props)
                state.upsert_element(element_id, props=props, html=rendered)
                manager.send(window_id, {
                    "type": "update_element",
                    "element_id": element_id,
                    "props": props,
                    "html": rendered,
                })
                value = new_value
            except ValueError:
                pass  # okänd/icke skrivbar variabel - ignorera klicket

    if bridge_loop is None:
        return

    from funktioner.queue import event_queue

    asyncio.run_coroutine_threadsafe(
        event_queue.put({
            "type": "html_action",
            "element_id": element_id,
            "action": action,
            "value": value,
            "window_id": window_id,
        }),
        bridge_loop,
    )


def _handle_element_clicked(window_id: str, element_id: str):
    """Klick på ett GUI-element. Just nu bara relevant för toggle-knappar
    (bundna till en variabel via create_toggle_button) - allt annat
    ignoreras tills vidare."""
    if not element_id:
        return

    el = state.get_element(element_id)
    if not el or el.get("type") != "toggle":
        return

    var_name = el.get("props", {}).get("variable")
    if not var_name:
        return

    from gui.backend.registry import ToolRegistry

    try:
        current_value = ToolRegistry.get_variable(var_name)
        new_value = not bool(current_value)
        ToolRegistry.set_variable(var_name, new_value)
    except ValueError:
        # Okänd eller icke skrivbar variabel - ignorera klicket istället
        # för att krascha websocket-hanteraren.
        return

    props = {**el.get("props", {}), "value": new_value}
    state.upsert_element(element_id, props=props)

    manager.send(
        window_id,
        {
            "type": "update_element",
            "element_id": element_id,
            "props": props,
        },
    )


