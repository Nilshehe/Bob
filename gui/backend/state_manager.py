"""
state_manager.py
Sparar hela GUI-tillståndet (fönster + element) till en JSON-fil så att
Bob:s GUI kan återställas exakt som det var, session efter session.
"""
import json
import threading
from pathlib import Path
from typing import Any, Dict

STATE_FILE = Path(__file__).parent.parent / "state" / "gui_state.json"

DEFAULT_STATE = {
    "windows": {},   # window_id -> {title, x, y, w, h, screen}
    "elements": {},  # element_id -> {type, window_id, x, y, w, h, visible, label, props}
    "stream_panel": {
        # Bobs live-svarswidget - permanent GUI-element, styrbart av Bob
        # via gui_tools.py (set_stream_panel/set_stream_panel_filters) och
        # av användaren via kugghjulet i frontend.
        "visible": True,
        # tab_hidden: True gömmer även den lilla "◈ live"-fliken (den
        # som annars alltid finns kvar som väg tillbaka in när panelen
        # är gömd). Enda vägen tillbaka då är snabbkommandot
        # Ctrl+Shift+L i frontend, eller att Bob sätter tillbaka det
        # via set_stream_panel.
        "tab_hidden": False,
        "x": None, "y": None, "w": None, "h": None,  # None = CSS-default (uppe till höger)
        "filters": {
            "text": True,
            "reasoning": True,
            "tool_call_chunk": True,
            "interrupt": True,
        },
        # Vilka fönster (window_id) som ska visa live-texten. Tom lista =
        # visa i alla öppna fönster (bakåtkompatibelt standardbeteende).
        "windows": [],
    },
    # Bobs centrala tema (theme.py) - bara accentfärgen sparas, resten
    # (background/surface/text/muted) räknas fram från den vid behov.
    "theme": {
        "accent": "#00eaff",
    },
}


class StateManager:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_STATE))

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    # ---- fönster ----
    def upsert_window(self, window_id: str, **fields):
        self.state["windows"].setdefault(window_id, {})
        self.state["windows"][window_id].update({k: v for k, v in fields.items() if v is not None})
        self.save()

    def remove_window(self, window_id: str):
        self.state["windows"].pop(window_id, None)
        # ta bort elementen som hörde till fönstret också
        self.state["elements"] = {
            eid: e for eid, e in self.state["elements"].items() if e.get("window_id") != window_id
        }
        self.save()

    # ---- element ----
    def upsert_element(self, element_id: str, **fields):
        self.state["elements"].setdefault(element_id, {})
        self.state["elements"][element_id].update({k: v for k, v in fields.items() if v is not None})
        self.save()

    def remove_element(self, element_id: str):
        self.state["elements"].pop(element_id, None)
        self.save()

    def get_element(self, element_id: str):
        return self.state["elements"].get(element_id)

    def all_elements_for_window(self, window_id: str):
        return {eid: e for eid, e in self.state["elements"].items() if e.get("window_id") == window_id}

    # ---- svarswidget (stream panel) ----
    def get_stream_panel(self):
        """Returnerar aktuellt state för svarswidgeten, med fallback till
        default om filen skrevs innan den här funktionen fanns."""
        default = json.loads(json.dumps(DEFAULT_STATE["stream_panel"]))
        panel = self.state.setdefault("stream_panel", default)
        panel.setdefault("filters", default["filters"])
        panel.setdefault("windows", default["windows"])
        panel.setdefault("tab_hidden", default["tab_hidden"])
        return panel

    def update_stream_panel(self, filters: Dict[str, Any] = None, **fields):
        panel = self.get_stream_panel()
        panel.update({k: v for k, v in fields.items() if v is not None})
        if filters:
            panel["filters"].update({k: v for k, v in filters.items() if v is not None})
        self.save()
        return panel


state = StateManager()
