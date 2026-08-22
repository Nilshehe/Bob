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
